import os
import json
import re
import googleapiclient.discovery
from datetime import datetime
from flask import Flask, render_template_string, request, url_for, jsonify
from google.cloud import storage
from werkzeug.utils import secure_filename
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT')
GCE_REGION = 'us-east1'
GCE_ZONE = 'us-east1-b' # MAKE SURE THIS IS YOUR VM's ZONE
GCE_INSTANCE_NAME = 'transcribe-worker-vm'
GCS_BUCKET_NAME = 'shaw-transcripts-20260207'
HUGGING_FACE_TOKEN = os.environ.get('HUGGING_FACE_TOKEN')

# --- FLASK APP INITIALIZATION ---
app = Flask(__name__)

# Client objects are now initialized inside each function
# This is a best practice for serverless environments (lazy initialization)

ALLOWED_EXTENSIONS = {'mp4', 'mp3', 'wav', 'flac', 'aac', 'ogg', 'webm', 'm4a'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# HTML content for the frontend is embedded here...
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Transcribe Anything</title>
    <style>
        body { font-family: sans-serif; margin: 20px; max-width: 800px; margin-left: auto; margin-right: auto; }
        h1, h2 { color: #333; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input[type="text"], input[type="file"] {
            width: 100%;
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-sizing: border-box;
            margin-bottom: 5px;
        }
        button {
            background-color: #007bff;
            color: white;
            padding: 10px 15px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        button:hover {
            background-color: #0056b3;
        }
        #message {
            margin-top: 15px;
            padding: 10px;
            border-radius: 4px;
            color: #333;
            background-color: #e2e3e5;
            display: none;
        }
        #message.success { background-color: #d4edda; color: #155724; }
        #message.error { background-color: #f8d7da; color: #721c24; }

        #transcriptionsList ul {
            list-style-type: none;
            padding: 0;
        }
        #transcriptionsList li {
            background-color: #f1f1f1;
            margin-bottom: 5px;
            padding: 10px;
            border-radius: 4px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        #transcriptionsList li a {
            color: #007bff;
            text-decoration: none;
        }
        #transcriptionsList li a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <h1>Transcribe Anything</h1>

    <div class="form-group">
        <label for="url">Video/Audio URL:</label>
        <input type="text" id="url" name="url" placeholder="e.g., https://www.youtube.com/watch?v=..." />
    </div>
    <div class="form-group">
        <label for="file">Or Upload File (Max 16MB):</label>
        <input type="file" id="file" name="file" accept="video/*,audio/*" />
    </div>
    <button id="transcribeButton">Transcribe</button>

    <div id="message" style="display: none;"></div>

    <h2>Previous Transcriptions:</h2>
    <div id="transcriptionsList">
        <ul></ul>
    </div>

    <script>
        const transcribeButton = document.getElementById('transcribeButton');
        const messageDiv = document.getElementById('message');
        const urlInput = document.getElementById('url');
        const fileInput = document.getElementById('file');
        const transcriptionsList = document.getElementById('transcriptionsList').querySelector('ul');

        function showMessage(msg, type) {
            messageDiv.textContent = msg;
            messageDiv.className = '';
            messageDiv.classList.add(type);
            messageDiv.style.display = 'block';
        }

        transcribeButton.addEventListener('click', async () => {
            messageDiv.style.display = 'none';

            let requestBody = new FormData();
            let hasInput = false;

            if (urlInput.value) {
                requestBody.append('url', urlInput.value);
                hasInput = true;
            }
            if (fileInput.files.length > 0) {
                const file = fileInput.files[0];
                if (file.size > 16 * 1024 * 1024) {
                    showMessage('File size exceeds 16MB limit.', 'error');
                    return;
                }
                requestBody.append('file', file);
                hasInput = true;
            }

            if (!hasInput) {
                showMessage('Please provide a URL or upload a file.', 'error');
                return;
            }

            showMessage('Starting transcription... This may take a while as the VM is spinning up.', '');
            transcribeButton.disabled = true;

            try {
                const response = await fetch('/transcribe', {
                    method: 'POST',
                    body: requestBody
                });

                const result = await response.text();

                if (response.ok) {
                    showMessage(result, 'success');
                    urlInput.value = '';
                    fileInput.value = '';
                    loadTranscriptions();
                } else {
                    showMessage('Error: ' + result, 'error');
                }
            } catch (error) {
                showMessage('Network error: ' + error.message, 'error');
                console.error('Transcription initiation error:', error);
            } finally {
                transcribeButton.disabled = false;
            }
        });

        async function loadTranscriptions() {
            transcriptionsList.innerHTML = '';
            try {
                const response = await fetch('/transcriptions');
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                const data = await response.json();
                if (data.length === 0) {
                    transcriptionsList.innerHTML = '<li>No previous transcriptions found.</li>';
                    return;
                }
                data.forEach(item => {
                    const listItem = document.createElement('li');
                    listItem.innerHTML = `<span>${item.name}</span> <a href="${item.download_url}" target="_blank" download>Download</a>`;
                    transcriptionsList.appendChild(listItem);
                });
            } catch (error) {
                console.error('Error loading transcriptions:', error);
                transcriptionsList.innerHTML = `<li style="color: red;">Error loading transcriptions: ${error.message}</li>`;
            }
        }

        loadTranscriptions();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/transcribe', methods=['POST'])
def transcribe():
    logger.info("Transcribe request received.")

    # Initialize clients inside the function
    storage_client = storage.Client()
    compute = googleapiclient.discovery.build('compute', 'v1')

    url = request.form.get('url')
    file = request.files.get('file')

    if not url and not file:
        logger.warning("No URL or file provided in transcribe request.")
        return "No URL or file provided", 400

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if file:
        source_base_name = secure_filename(file.filename).rsplit('.', 1)[0]
    elif url:
        source_base_name = re.sub(r'https?://', '', url)
        source_base_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', source_base_name)[:50]
    else:
        source_base_name = "untitled"

    transcription_id = f"{source_base_name}_{timestamp}"

    input_source_for_vm = ""

    if file:
        if not allowed_file(file.filename):
            logger.warning(f"Invalid file type uploaded: {file.filename}")
            return "Invalid file type", 400

        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        gcs_file_path = f"{transcription_id}/{secure_filename(file.filename)}"
        blob = bucket.blob(gcs_file_path)

        file.stream.seek(0)
        blob.upload_from_file(file.stream, content_type=file.content_type)
        logger.info(f"File {file.filename} uploaded to GCS: {gcs_file_path}")
        input_source_for_vm = f"gs://{GCS_BUCKET_NAME}/{gcs_file_path}"
    elif url:
        logger.info(f"URL provided for transcription: {url}")
        input_source_for_vm = url

    try:
        metadata_body = {
            "items": [
                {"key": "gcs-input-path", "value": input_source_for_vm},
                {"key": "gcs-output-bucket", "value": GCS_BUCKET_NAME},
                {"key": "huggingface-token", "value": HUGGING_FACE_TOKEN},
                {"key": "transcription-id", "value": transcription_id}
            ]
        }

        instance_info = compute.instances().get(project=GCP_PROJECT_ID, zone=GCE_ZONE, instance=GCE_INSTANCE_NAME).execute()
        metadata_fingerprint = instance_info['metadata']['fingerprint']

        logger.info(f"Setting metadata for instance {GCE_INSTANCE_NAME}...")
        compute.instances().setMetadata(
            project=GCP_PROJECT_ID,
            zone=GCE_ZONE,
            instance=GCE_INSTANCE_NAME,
            body={'fingerprint': metadata_fingerprint, 'items': metadata_body['items']}
        ).execute()

        logger.info(f"Starting instance {GCE_INSTANCE_NAME}...")
        compute.instances().start(
            project=GCP_PROJECT_ID,
            zone=GCE_ZONE,
            instance=GCE_INSTANCE_NAME
        ).execute()
        logger.info("Instance start command sent successfully.")

        return f"Transcription job '{transcription_id}' initiated. The VM is spinning up.", 200

    except Exception as e:
        logger.error(f"Error starting VM or setting metadata: {e}")
        return f"Failed to initiate transcription: {e}", 500

@app.route('/transcriptions')
def list_transcriptions():
    logger.info("Listing transcriptions.")
    storage_client = storage.Client()

    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        transcriptions_data = []

        blobs = storage_client.list_blobs(bucket.name, delimiter='/')
        for page in blobs.pages:
            for prefix in page.prefixes:
                folder_name = prefix[:-1]

                blobs_in_folder = storage_client.list_blobs(bucket.name, prefix=prefix)
                txt_file_blob = next((blob for blob in blobs_in_folder if blob.name.endswith('.txt')), None)

                if txt_file_blob:
                    download_url = url_for('download_file', path=txt_file_blob.name, _external=True)
                    transcriptions_data.append({'name': folder_name, 'download_url': download_url})

        transcriptions_data.sort(key=lambda x: x['name'], reverse=True)

        return jsonify(transcriptions_data)
    except Exception as e:
        logger.error(f"Error listing transcriptions: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/download/<path:path>')
def download_file(path):
    logger.info(f"Download request for path: {path}")
    storage_client = storage.Client()

    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(path)

        if not blob.exists():
            logger.warning(f"File not found for download: {path}")
            return "File not found", 404

        response = app.response_class(
            blob.download_as_bytes(),
            mimetype=blob.content_type if blob.content_type else 'application/octet-stream'
        )
        response.headers.set('Content-Disposition', 'attachment', filename=os.path.basename(path))
        return response
    except Exception as e:
        logger.error(f"Error serving file: {e}")
        return f"Failed to serve file: {e}", 500
