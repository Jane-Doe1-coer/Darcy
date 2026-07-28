from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS
import tempfile
import os
import traceback

# Import the JJ module (adjacent JJ.py). It must be importable from this directory.
import JJ

app = Flask(__name__)
# Allow cross-origin requests from any origin for local development
CORS(app)


@app.route('/api/darcy', methods=['POST'])
def api_darcy():
    try:
        data = request.get_json(force=True)
    except Exception:
        data = request.get_json(silent=True) or {}

    text = (data or {}).get('text', '')
    if not isinstance(text, str) or not text.strip():
        return jsonify({'response': ''})

    try:
        reply = JJ.generate_text_reply(text)
        # Fallback to an empty string if None
        if reply is None:
            reply = ""
        return jsonify({'response': reply})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'response': '', 'error': str(e)}), 500


@app.route('/api/tts')
def api_tts():
    text = request.args.get('text', '')
    if not text:
        return abort(400, "Missing 'text' parameter")

    # Try to use edge-tts to synthesize an mp3 and return it
    try:
        import asyncio
        import edge_tts

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        tmp_path = tmp.name
        tmp.close()

        async def gen():
            # Use a high-quality voice (adjust as desired)
            communicate = edge_tts.Communicate(text, "en-US-JennyNeural")
            await communicate.save(tmp_path)

        asyncio.run(gen())

        # Return file and let OS clean it up later
        return send_file(tmp_path, mimetype='audio/mpeg')

    except Exception as e:
        traceback.print_exc()
        return abort(500, description=f"TTS generation failed: {e}")


if __name__ == '__main__':
    # Run development server on port 8000 to match the HTML's expectations
    app.run(host='127.0.0.1', port=8000, debug=True)
