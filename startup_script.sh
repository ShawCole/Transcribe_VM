#!/bin/bash
set -euxo pipefail

# Define variables from VM metadata
GCS_INPUT_PATH=$(curl "http://metadata.google.internal/computeMetadata/v1/instance/attributes/gcs-input-path" -H "Metadata-Flavor: Google")
GCS_OUTPUT_BUCKET=$(curl "http://metadata.google.internal/computeMetadata/v1/instance/attributes/gcs-output-bucket" -H "Metadata-Flavor: Google")
HF_TOKEN=$(curl "http://metadata.google.internal/computeMetadata/v1/instance/attributes/huggingface-token" -H "Metadata-Flavor: Google")
TRANSCRIPTION_ID=$(curl "http://metadata.google.internal/computeMetadata/v1/instance/attributes/transcription-id" -H "Metadata-Flavor: Google")

# Set Hugging Face token as environment variable
export HF_TOKEN="YOUR_HUGGING_FACE_TOKEN" # <-- REPLACE THIS with your actual token

# Ensure gcloud CLI is installed and updated for gsutil access
sudo apt-get update
sudo apt-get install -y google-cloud-sdk # Ensure gcloud CLI is available for gsutil

# Install dependencies
sudo apt-get install -y python3-pip ffmpeg

# Install transcribe-anything
pip3 install transcribe-anything

# Create a working directory
WORKDIR="/tmp/transcription_work/${TRANSCRIPTION_ID}"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

INPUT_SOURCE="" # Initialize INPUT_SOURCE
SOURCE_FILENAME="" # To store filename if downloaded

# Determine input source (GCS path or URL)
if [[ "$GCS_INPUT_PATH" == gs://* ]]; then
  # It's a GCS path, download the file
  echo "Downloading from GCS: $GCS_INPUT_PATH"
  SOURCE_FILENAME=$(basename "$GCS_INPUT_PATH")
  gsutil cp "$GCS_INPUT_PATH" "$SOURCE_FILENAME"
  INPUT_SOURCE="$SOURCE_FILENAME"
elif [[ "$GCS_INPUT_PATH" == http* ]]; then
  # It's a URL, use it directly
  echo "Using URL as input: $GCS_INPUT_PATH"
  INPUT_SOURCE="$GCS_INPUT_PATH"
else
  echo "Error: GCS_INPUT_PATH metadata attribute is invalid: $GCS_INPUT_PATH"
  # Exit with error and shut down
  sudo shutdown -h now
  exit 1
fi

echo "Starting transcription for: $INPUT_SOURCE"
# Run transcribe-anything with diarization.
# --source parameter is now used instead of --url from previous version
# The --diarize flag enables speaker diarization
transcribe-anything --source "$INPUT_SOURCE" --output_dir . --diarize

# Find the generated .txt file
OUTPUT_FILE=$(find . -name "*.txt" -print -quit)

if [ -n "$OUTPUT_FILE" ]; then
  echo "Found transcription file: $OUTPUT_FILE"
  # Upload transcription output to GCS
  gsutil cp "$OUTPUT_FILE" "gs://shaw-transcripts-20260207/${TRANSCRIPTION_ID}/$(basename "$OUTPUT_FILE")" # <-- REPLACE THIS with your GCS bucket name
  echo "Transcription uploaded to gs://shaw-transcripts-20260207/${TRANSCRIPTION_ID}/$(basename "$OUTPUT_FILE")" # <-- REPLACE THIS too
else
  echo "Error: No transcription .txt file found in $WORKDIR. Check transcribe-anything logs."
fi

# Shut down the VM
echo "Transcription job complete. Shutting down VM."
sudo shutdown -h now
