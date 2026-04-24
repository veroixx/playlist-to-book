# playlist-to-book
A Python desktop app that extracts transcripts from a YouTube playlist or single video and uses the Groq API to convert them into a structured, readable book in Markdown format.

It also supports manually uploading transcript files, so you can generate books without fetching transcripts directly from YouTube.

### Requirements
Python 3.9+
Groq LLM API key

### Installation
Bash
pip install -r requirements.txt

### Usage
Bash
python main.py

### In the GUI:
* Enter the YouTube playlist or video URL.
* Enter a title for your book.
* Set the output language (e.g., English).
* Choose where to save the transcript cache and book output files.
* Enter your Groq LLM API key.
* Click Generate Book and select a Groq model when prompted.
* #Manual Upload: If you already have a transcript, upload it to the transcript cache and select "Already have transcript" instead of "Generate Book."

### Note:
Output is saved as a .txt file containing Markdown-formatted book chapters — one chapter per video.

### How It Works
* The transcript of every video in the playlist is fetched from YouTube.
* Each transcript is split into 3000-word chunks.
* Each chunk is sent to Groq with a book-chapter prompt, using the previous chunk's output as context for continuity.
* The refined chapters are appended into a single output file.

### Output Format
* Each video becomes a book chapter with:
* Clear headings and subheadings.
* Bullet points where appropriate.
* Blockquote definitions for technical terms and jargon.
* The output file works well with Obsidian and as a source in NotebookLM.

### Notes
* The .env file can store API_KEY and LANGUAGE to pre-fill those fields on launch.
* Processing can be cancelled at any time using the Cancel button.
* If a video has no transcript available, it is skipped and processing continues.
