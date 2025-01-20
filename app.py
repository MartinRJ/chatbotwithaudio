import gradio as gr
import requests
import json
import os
import boto3
import time
import wave

# 🔹 S3-Configuration
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
BUCKET_NAME = "chatbot-storage-2025-01-17"
REGION_NAME = "eu-north-1"

s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=REGION_NAME
)

# 🔹 Multi-turn conversation history
conversation_history = []

# 🔹 Uploading audio files to S3
def upload_to_s3(file_path):
    """Uploads an audio file to S3 and returns the public URL."""
    if not os.path.exists(file_path):
        return None  # File does not exist, return None

    file_name = f"audio_{int(time.time())}.wav"
    s3_client.upload_file(file_path, BUCKET_NAME, file_name)
    public_url = f"https://{BUCKET_NAME}.s3.{REGION_NAME}.amazonaws.com/{file_name}"
    return public_url

def format_conversation_for_gradio(conversation_history):
    """Formats the chat history for Gradio in case of an error."""
    formatted_conversation = []
    for entry in conversation_history:
        role = entry["role"]
        
        # Extract content based on its structure
        if isinstance(entry["content"], list):
            if len(entry["content"]) > 0 and isinstance(entry["content"][0], dict):
                content = entry["content"][0].get("text", repr(entry["content"][0]))  # If no "text" key exists, store the content as a string
            else:
                content = repr(entry["content"])  # Store content as a string if it's an empty list or an unexpected format
        elif isinstance(entry["content"], dict):
            content = entry["content"].get("text", repr(entry["content"]))  # If it's a dictionary, safely access "text"
        else:
            content = str(entry["content"])  # Fallback: Convert content to string

        formatted_conversation.append({
            "role": role,
            "content": content
        })

    # Returns the formatted conversation without additional debug output
    return formatted_conversation

def chat_with_api(user_input, audio_file, API_URL, API_KEY):
    global conversation_history

    # Prepare the API request structure
    user_content = []

    # Ignore empty messages (no text & no audio)
    if not user_input.strip() and not audio_file:
        return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value=None, visible=False)

    # Add text input if provided
    if isinstance(user_input, str) and user_input.strip():
        user_content.append({"type": "text", "text": user_input})

    # Process audio file if provided
    s3_url = None
    if audio_file:
        if not isinstance(audio_file, str) or not os.path.exists(audio_file):
            return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ Error: Invalid or missing audio file.", visible=True)

        file_size = os.path.getsize(audio_file)
        if file_size < 2048:
            return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ Error: Audio file too small.", visible=True)

        try:
            with wave.open(audio_file, "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                num_frames = wav_file.getnframes()
                duration = num_frames / float(frame_rate)
                if duration < 0.5:
                    return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ Error: Recording too short.", visible=True)
        except wave.Error:
            return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ Error: Unable to process audio file.", visible=True)
        except Exception:
            return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ Error: Unexpected error while processing audio.", visible=True)

        # Upload audio file to S3
        s3_url = upload_to_s3(audio_file)
        if s3_url:
            user_content.append({"type": "audio_url", "audio_url": {"url": s3_url}})
        else:
            return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ Error: Audio upload failed.", visible=True)

    # Abort if no valid input is provided
    if not user_content:
        return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ Error: Empty message.", visible=True)

    # Append new user message to conversation history
    conversation_history.append({"role": "user", "content": user_content})

    # Validate conversation history structure
    if not all("role" in message and "content" in message for message in conversation_history):
        return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ Error: Invalid conversation structure.", visible=True)

    # Construct API request payload
    data = {
        "input_data": {
            "input_string": conversation_history
        }
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {API_KEY}'
    }

    # Send request to the API
    try:
        response = requests.post(API_URL, json=data, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value=f"⚠️ API Error: {e}", visible=True)
    except requests.exceptions.RequestException:
        return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ API request failed.", visible=True)

    # Parse API response
    response_text = response.text.strip()
    try:
        api_response = response.json()
        if isinstance(api_response, str):
            api_response = json.loads(api_response)
    except json.JSONDecodeError:
        return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value="⚠️ API Error: Invalid JSON response.", visible=True)

    # Handle API error responses
    if "error" in api_response:
        error_message_text = api_response["error"].strip() or "⚠️ API Error: No response"
        return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value=error_message_text, visible=True)
    
    # Process API response and update conversation history
    bot_reply = api_response.get("response", None)
    if isinstance(bot_reply, str):
        conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": bot_reply}]})

    # Format final response for Gradio
    return format_conversation_for_gradio(conversation_history), gr.update(value=None), gr.update(value=None, visible=False)

def reset_conversation():
    """Resets the chat history and clears the UI (including audio input)."""
    global conversation_history
    conversation_history.clear()

    # Reset UI: Clear chat history and audio input
    return gr.update(value=[]), gr.update(value=None)  # Reset chat and audio

with gr.Blocks() as demo:
    gr.Markdown("# 🔊 Chatbot with Audio & Text")

    chatbot = gr.Chatbot(type="messages")
    text_input = gr.Textbox(placeholder="Enter your message...")
    error_message = gr.Markdown(visible=False)  # 🔴 Error message (hidden by default)

    audio_input = gr.Audio(
        sources=["microphone"], 
        type="filepath", 
        format="wav", 
        autoplay=False, 
        interactive=True
    )

    submit_button = gr.Button("Send")
    reset_button = gr.Button("Reset Chat")  
    reset_button.click(reset_conversation, outputs=[chatbot, audio_input])  # Resets chat and audio

    api_url_input = gr.Textbox(label="API Endpoint URL")
    api_key_input = gr.Textbox(label="API Auth Token", type="password")

    submit_button.click(chat_with_api, inputs=[text_input, audio_input, api_url_input, api_key_input], outputs=[chatbot, audio_input, error_message])

demo.launch()
