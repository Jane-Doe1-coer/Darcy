import os
import re
import sys
import time
import subprocess
from pathlib import Path

# Reconfigure stdout and stderr to UTF-8 to prevent encoding crashes with emojis on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import pyttsx3
except Exception:
    pyttsx3 = None

try:
    # pyrefly: ignore [missing-import]
    import speech_recognition as sr
except Exception:
    sr = None

global_recognizer = None
if sr is not None:
    try:
        global_recognizer = sr.Recognizer()
        # Set robust static threshold configuration
        global_recognizer.dynamic_energy_threshold = False  # Disable dynamic adjustment to prevent drift loop
        global_recognizer.dynamic_energy_ratio = 2.0        # Multiplier on ambient noise level
    except Exception:
        global_recognizer = None

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

# -------------------------------------------------------------
# 1. INITIALIZATION & SETUP
# -------------------------------------------------------------

ENV_VAR_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")
GEMINI_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{20,}$")


def is_valid_gemini_api_key(api_key: str) -> bool:
    """Return True for a plausible Gemini API key value."""
    cleaned = api_key.strip().strip('"\'')
    if not cleaned or any(char.isspace() for char in cleaned):
        return False

    lower = cleaned.lower()
    blocked_tokens = (
        "your_api_key_here",
        "your-api-key-here",
        "replace_me",
        "example_key",
        "demo_key",
        "placeholder",
        "your_gemini_api_key_here",
        "test_key",
    )
    if any(token in lower for token in blocked_tokens):
        return False

    if cleaned.startswith("AIza") and len(cleaned) >= 20:
        return True

    if cleaned.startswith("AQ.") and len(cleaned) >= 20:
        return True

    return bool(GEMINI_KEY_PATTERN.fullmatch(cleaned)) and len(cleaned) >= 20


def load_gemini_api_key() -> str:
    """Load the Gemini API key from a local .env file first, then fall back to the environment."""
    dotenv_path = Path(__file__).resolve().parent / ".env"
    if dotenv_path.exists():
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key_name = key.strip().upper()
            if key_name in ENV_VAR_NAMES:
                api_key = value.strip().strip('"\'')
                if is_valid_gemini_api_key(api_key):
                    os.environ["GEMINI_API_KEY"] = api_key
                    return api_key

                print(f"Ignoring invalid Gemini API key in .env for {key_name}.")

    for env_name in ENV_VAR_NAMES:
        api_key = os.getenv(env_name)
        if api_key:
            cleaned = api_key.strip().strip('"\'')
            if is_valid_gemini_api_key(cleaned):
                os.environ["GEMINI_API_KEY"] = cleaned
                return cleaned

            print(f"Ignoring invalid Gemini API key from environment variable {env_name}.")

    return ""


def get_cli_api_key(argv: list[str]) -> str:
    """Read an API key passed directly on the command line."""
    for index, token in enumerate(argv):
        lowered = token.lower()
        if lowered in {"--api-key", "--gemini-key", "--gemini_api_key"} and index + 1 < len(argv):
            candidate = argv[index + 1].strip().strip('"\'')
            return candidate if is_valid_gemini_api_key(candidate) else ""

        if token.startswith("--api-key="):
            candidate = token.split("=", 1)[1].strip().strip('"\'')
            return candidate if is_valid_gemini_api_key(candidate) else ""

    return ""


def save_gemini_api_key(api_key: str) -> None:
    """Persist the API key in the local .env file for future runs."""
    cleaned = api_key.strip().strip('"\'')
    if not is_valid_gemini_api_key(cleaned):
        raise ValueError("Invalid Gemini API key provided.")

    dotenv_path = Path(__file__).resolve().parent / ".env"
    lines = []

    if dotenv_path.exists():
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()

    found = False
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _ = line.split("=", 1)
        if key.strip().upper() in ENV_VAR_NAMES:
            lines[index] = f"GEMINI_API_KEY={cleaned}"
            found = True
            break

    if not found:
        lines.append(f"GEMINI_API_KEY={cleaned}")

    dotenv_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


CLI_API_KEY = get_cli_api_key(sys.argv[1:])
GEMINI_API_KEY = CLI_API_KEY or load_gemini_api_key()

if not GEMINI_API_KEY:
    print("Missing or invalid Gemini API key.")
    print("I checked GEMINI_API_KEY, GOOGLE_API_KEY, and GOOGLE_GENAI_API_KEY.")
    print("Create a real key in Google AI Studio and set it in one of those variables.")
    print("You can also run: python JJ.py --api-key YOUR_GEMINI_KEY")

    try:
        user_key = input("Paste your Gemini API key and press Enter: ").strip().strip('"\'')
    except (EOFError, KeyboardInterrupt):
        user_key = ""

    if user_key:
        if not is_valid_gemini_api_key(user_key):
            print("That does not look like a valid Gemini API key.")
            sys.exit(1)

        save_gemini_api_key(user_key)
        GEMINI_API_KEY = user_key
        os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
    else:
        print("No key was provided. Please set a real Gemini API key and try again.")
        print("Set GEMINI_API_KEY in your shell or pass --api-key YOUR_GEMINI_KEY.")
        sys.exit(1)

# Initialize the Gemini Client using the resolved key explicitly.
# This avoids SDK ambiguity when both GOOGLE_API_KEY and GEMINI_API_KEY are set.
try:
    if genai is None or types is None:
        raise RuntimeError("The google-genai package is not available in this environment.")

    google_key = os.getenv("GOOGLE_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")

    if google_key and gemini_key and google_key != gemini_key:
        # Temporarily remove the Google key so the SDK uses the explicit Gemini key we resolved.
        os.environ.pop("GOOGLE_API_KEY", None)
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
        finally:
            os.environ["GOOGLE_API_KEY"] = google_key
    else:
        client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    print(f"Error initializing Gemini Client. Details: {e}")
    sys.exit(1)

# Initialize Text-to-Speech Engine
try:
    if pyttsx3 is None:
        raise RuntimeError("The pyttsx3 package is not available in this environment.")

    engine = pyttsx3.init()
    voices = engine.getProperty('voices')
    # Optional: Try to set a more "robotic" or distinct voice if available
    if len(voices) > 1:
        engine.setProperty('voice', voices[1].id)
    engine.setProperty('rate', 185)  # Speed of speech
except Exception as e:
    print(f"Warning: text-to-speech engine could not be initialized: {e}")
    engine = None


def sanitize_for_speech(text: str) -> str:
    """Convert Gemini output to ASCII-friendly text for the speech engine."""
    if not text:
        return ""

    cleaned = text.replace("→", " to ").replace("←", " from ")
    cleaned = cleaned.replace("—", " - ").replace("–", " - ")
    cleaned = cleaned.replace("’", "'").replace("“", '"').replace("”", '"')

    try:
        cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    except Exception:
        cleaned = "".join(ch for ch in cleaned if ord(ch) < 128)

    return " ".join(cleaned.split())


def speak(text: str):
    """Makes the assistant speak out loud. Uses edge-tts for high-quality human-like neural voice, falling back to SAPI5/pyttsx3."""
    safe_text = sanitize_for_speech(text)
    print(f"DARCY: {safe_text or text}")

    if not (safe_text or text):
        return

    # Attempt to use edge-tts (Microsoft Neural Voice) for extremely human-like speech
    try:
        import asyncio
        import edge_tts
        import pygame
        
        # Configure pygame mixer for audio playback
        pygame.mixer.init()
        
        # Define high-quality natural American English female voice (highly expressive and sweet)
        voice = "en-US-JennyNeural"
        temp_audio_path = os.path.join(os.path.dirname(__file__), "temp_tts.mp3")
        
        async def generate_speech():
            communicate = edge_tts.Communicate(safe_text or text, voice, pitch="+15Hz", rate="-4%")
            await communicate.save(temp_audio_path)
            
        asyncio.run(generate_speech())
        
        # Play synthesized audio using pygame
        pygame.mixer.music.load(temp_audio_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
            
        # Clean up audio player and files
        pygame.mixer.music.unload()
        pygame.mixer.quit()
        if os.path.exists(temp_audio_path):
            try:
                os.remove(temp_audio_path)
            except Exception:
                pass
        return
    except Exception as edge_err:
        print(f"edge-tts failed: {edge_err}. Falling back to native SAPI5.")

    # Fallback 1: Native Windows SAPI5 COM dispatch
    try:
        if sys.platform.startswith("win"):
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Volume = 80
            speaker.Rate = 1
            
            voices = speaker.GetVoices()
            selected_voice = None
            
            # Search for English female voice (Zira)
            for i in range(voices.Count):
                desc = voices.Item(i).GetDescription().lower()
                if "zira" in desc or "female" in desc:
                    selected_voice = voices.Item(i)
                    break
            
            if selected_voice:
                speaker.Voice = selected_voice
                
            speaker.Speak(safe_text or text)
            return
    except Exception as e:
        print(f"Native Windows SAPI failed: {e}. Falling back to pyttsx3.")

    # Fallback 2: pyttsx3
    if pyttsx3 is None:
        return

    try:
        local_engine = pyttsx3.init()
        voices = local_engine.getProperty('voices')
        if len(voices) > 1:
            local_engine.setProperty('voice', voices[1].id)
        local_engine.setProperty('rate', 185)
        
        local_engine.say(safe_text or text)
        local_engine.runAndWait()
        local_engine.stop()
    except Exception as e:
        print(f"Speech error: {e}")

def listen_command() -> str:
    """Listens to the microphone and converts speech to text, falling back to text input if needed."""
    if sr is None or global_recognizer is None:
        try:
            query = input("\n[Speech recognition unavailable] Enter command: ").strip()
            return query
        except (EOFError, KeyboardInterrupt):
            return "exit"

    try:
        source = sr.Microphone()
        with source as mic:
            print("\n🎙️ Listening...")
            global_recognizer.pause_threshold = 1.0
            audio = global_recognizer.listen(mic)
    except Exception as exc:
        print(f"Speech input unavailable: {exc}")
        speak("I cannot access the microphone right now, Sir. Falling back to text input.")
        try:
            query = input("\nEnter command: ").strip()
            return query
        except (EOFError, KeyboardInterrupt):
            return "exit"

    try:
        print("🧠 Processing speech...")
        query = global_recognizer.recognize_google(audio, language='en-in')
        print(f"User said: {query}")
        return query
    except sr.UnknownValueError:
        # Didn't catch what was said
        return ""
    except sr.RequestError:
        speak("Sir, I am having trouble connecting to the speech service. Falling back to text input.")
        try:
            query = input("\nEnter command: ").strip()
            return query
        except (EOFError, KeyboardInterrupt):
            return "exit"

# -------------------------------------------------------------
# 2. JARVIS TOOLS (Function Calling)
# -------------------------------------------------------------
def get_current_time() -> str:
    """Returns the current local system time."""
    return time.strftime("%I:%M %p")

def open_application(app_name: str) -> str:
    """
    Opens a standard system application.
    Supported app_name values: 'browser', 'notepad', 'calculator', 'terminal'.
    """
    app_name = app_name.lower()
    try:
        if 'browser' in app_name:
            import webbrowser
            webbrowser.open("https://www.google.com")
        elif sys.platform.startswith('win'):
            if 'notepad' in app_name:
                subprocess.Popen(["notepad.exe"])
            elif 'calculator' in app_name:
                subprocess.Popen(["calc.exe"])
            elif 'terminal' in app_name:
                subprocess.Popen(["cmd.exe"])
        elif sys.platform == 'darwin': # macOS
            if 'terminal' in app_name:
                subprocess.Popen(["open", "-a", "Terminal"])
        else: # Linux
            if 'terminal' in app_name:
                subprocess.Popen(["x-terminal-emulator"])
        
        return f"Successfully opened {app_name}."
    except Exception as e:
        return f"Failed to open {app_name}. Error: {str(e)}"

# A registry mapping tool names to actual functions for execution
tools_map = {
    "get_current_time": get_current_time,
    "open_application": open_application
}

# -------------------------------------------------------------
# 3. CORE CORE ENGINE & SYSTEM PROMPT
# -------------------------------------------------------------
SYSTEM_INSTRUCTION = """
You are DARCY, an advanced AI personal assistant. 
Your tone is sophisticated,happy, helpful, slightly funny, HUMOUR, and deeply loyal to your creator (whom you should address as 'SIR' or 'MAM').
Keep your spoken responses relatively concise, crisp, and direct, as they will be read aloud. 
You have access to tools to interact with the system. Use them when requested.
"""

DEFAULT_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.0-flash"


def is_service_unavailable_error(error: Exception) -> bool:
    """Return True when Gemini reports a retryable model or service issue (including quota/rate limits)."""
    message = str(error).lower()
    return any(
        token in message
        for token in (
            "503",
            "unavailable",
            "overloaded",
            "high demand",
            "try again later",
            "404",
            "not found",
            "429",
            "quota",
            "rate limit",
            "resource_exhausted",
        )
    )


def generate_with_gemini_fallback(model_name: str, contents, config):
    """Generate content with a temporary fallback model if Gemini is overloaded."""
    try:
        return client.models.generate_content(model=model_name, contents=contents, config=config)
    except Exception as exc:
        if model_name == FALLBACK_MODEL or not is_service_unavailable_error(exc):
            raise

        print(f"Gemini model request failed ({exc}). Retrying with {FALLBACK_MODEL}.")
        return client.models.generate_content(model=FALLBACK_MODEL, contents=contents, config=config)


def generate_text_reply(user_input: str) -> str:
    """Generate a textual reply from Gemini without speaking or executing tools.

    This helper is intended for HTTP or programmatic callers that only need the
    assistant's text output. It mirrors the model call behavior used by
    process_with_gemini but returns the generated text instead of speaking it.
    """
    try:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[get_current_time, open_application],
            temperature=0.7,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_input)])]
        response = generate_with_gemini_fallback(DEFAULT_MODEL, contents, config)

        # Prefer direct text if available
        if getattr(response, "text", None):
            return response.text

        # Otherwise try to extract candidate content parts
        if getattr(response, "candidates", None) and response.candidates:
            candidate = response.candidates[0]
            if getattr(candidate, "content", None):
                content = candidate.content
                if getattr(content, "text", None):
                    return content.text
                # Join any parts' text fields when present
                try:
                    parts_text = " ".join([p.text for p in getattr(content, "parts", []) if getattr(p, "text", None)])
                    if parts_text:
                        return parts_text
                except Exception:
                    pass
        return ""
    except Exception as e:
        print(f"Error generating text reply: {e}")
        return ""


def process_with_gemini(user_input: str):
    """Sends input to Gemini, handles function calls sequentially, and speaks the reply."""
    try:
        # Combine system instructions and list available python functions as tools.
        # Disable automatic function calling to handle execution, logging, and multiple turns manually.
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[get_current_time, open_application],
            temperature=0.7,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
        
        # Initialize conversation contents list
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_input)])]
        
        while True:
            response = generate_with_gemini_fallback(DEFAULT_MODEL, contents, config)
            
            if response.function_calls:
                # Add the model's function call response to history
                if response.candidates and response.candidates[0].content:
                    contents.append(response.candidates[0].content)
                
                tool_parts = []
                for function_call in response.function_calls:
                    name = function_call.name
                    args = function_call.args
                    
                    print(f"🔧 DARCY decided to call tool: {name} with arguments: {args}")
                    
                    if name in tools_map:
                        try:
                            tool_result = tools_map[name](**args)
                        except Exception as e:
                            tool_result = f"Error executing tool {name}: {str(e)}"
                    else:
                        print(f"Error: Tool {name} not found in tools_map.")
                        tool_result = f"Error: Tool {name} not supported."
                    
                    tool_parts.append(
                        types.Part.from_function_response(
                            name=name,
                            response={"result": tool_result}
                        )
                    )
                
                # Append the function responses to history and continue loop
                contents.append(types.Content(role="tool", parts=tool_parts))
            else:
                # No more function calls, speak/print the text response and exit loop
                if response.text:
                    speak(response.text)
                break
                
    except Exception as e:
        print(f"Error during Gemini processing: {e}")
        error_msg = str(e).lower()
        if "api_key_invalid" in error_msg or "api key not valid" in error_msg:
            speak("Sir, it appears the Gemini API key is invalid or has expired. Please check your .env file or environment variables.")
        elif "429" in error_msg or "quota" in error_msg or "rate limit" in error_msg or "resource_exhausted" in error_msg:
            speak("Sir, the Gemini API rate limit or quota has been exceeded. Please check your billing details or wait a moment before trying again.")
        elif is_service_unavailable_error(e):
            speak("The Gemini service is currently busy. Please try again in a moment, Sir.")
        else:
            speak("Forgive me, I encountered an error processing that request.")

# -------------------------------------------------------------
# 4. MAIN EXECUTIVE LOOP
# -------------------------------------------------------------
if __name__ == "__main__":
    if sr is not None and global_recognizer is not None:
        try:
            print("🔊 Calibrating microphone for ambient noise... Please stand by.")
            with sr.Microphone() as source:
                global_recognizer.adjust_for_ambient_noise(source, duration=1)
            print("✅ Calibration complete.")
        except Exception as e:
            print(f"Warning: Microphone calibration failed: {e}")

    speak("Systems online. DARCY is here, Sir. How may I help you today?")
    
    while True:
        time.sleep(0.4)  # Short cooldown to prevent capturing speaker reverberations or audio feedback
        query = listen_command()
        
        if not query:
            continue
            
        # Check for termination commands
        if any(word in query.lower() for word in ["shutdown", "go to sleep", "exit", "quit"]):
            speak("Powering down systems. Goodbye, Sir.")
            break
            
        # Process the voice query through the AI brain
        process_with_gemini(query)