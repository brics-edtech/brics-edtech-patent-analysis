import asyncio
import json
import os
import logging
import signal
import re
from pathlib import Path

import nest_asyncio
from tqdm.asyncio import tqdm
from aiolimiter import AsyncLimiter

from g4f.client import AsyncClient

# Patch nested event loops (useful for interactive environments)
nest_asyncio.apply()

# ------------------------------------------------------------------------------
# Configuration and Constants
# ------------------------------------------------------------------------------
MAX_CONCURRENT_REQUESTS = 30   # Maximum concurrent API calls
RATE_LIMIT = 50                # Maximum requests per RATE_PERIOD seconds
RATE_PERIOD = 1
# Set the directory where the JSON files are found (recursively)
INPUT_DIR = "patents_csvs/json_output"

# Global shutdown flag
shutdown_requested = False

# ------------------------------------------------------------------------------
# Logging Configuration
# ------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Helper Function: Extract JSON from text (e.g., markdown-wrapped JSON)
# ------------------------------------------------------------------------------
def extract_json(text: str) -> str:
    """
    Attempts to extract a JSON object from a string that may include markdown formatting.
    For example, if the API response is wrapped in triple backticks, then extract the JSON.
    """
    # Try to find JSON within triple backticks with an optional language specifier.
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    # Try to find any triple-backtick content and check if it looks like JSON.
    match = re.search(r"```(.*?)```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate
    # Otherwise, return the raw text after stripping extra whitespace.
    return text.strip()

# ------------------------------------------------------------------------------
# Signal Handling for Graceful Shutdown
# ------------------------------------------------------------------------------
def handle_shutdown():
    global shutdown_requested
    if not shutdown_requested:
        logger.info("Shutdown requested. Cancelling tasks gracefully...")
        shutdown_requested = True

def setup_signal_handlers(loop: asyncio.AbstractEventLoop):
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown)

# ------------------------------------------------------------------------------
# Asynchronous File Utilities
# ------------------------------------------------------------------------------
async def async_read_json(file_path: Path):
    """Asynchronously reads a JSON file and returns its content as a Python object."""
    def read_json():
        return json.loads(file_path.read_text(encoding="utf-8"))
    try:
        return await asyncio.to_thread(read_json)
    except Exception as e:
        logger.error(f"Failed reading JSON file {file_path}: {e}")
        raise

async def async_write_text(file_path: str, text: str):
    """Asynchronously writes text to a file."""
    try:
        await asyncio.to_thread(lambda: Path(file_path).write_text(text, encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to write file {file_path}: {e}")
        raise

# ------------------------------------------------------------------------------
# Asynchronous Function to Call g4f Model with an Improved Prompt
# ------------------------------------------------------------------------------
async def async_get_teaching_content(client: AsyncClient, text, semaphore: asyncio.Semaphore, limiter: AsyncLimiter):
    """
    Uses the g4f model to determine if the given text relates to the educational process.
    The prompt asks for a JSON response with a key "teaching_content" (true/false).
    """
    # Ensure text is a string.
    if isinstance(text, list):
        text = "\n\n".join(str(part) for part in text)
    elif not isinstance(text, str):
        text = str(text)

    if not text.strip():
        return False

    # Escape any literal curly braces in text to prevent f-string formatting issues.
    text_safe = text.replace("{", "{{").replace("}", "}}")

    prompt = f"""
Please analyze the following text and determine whether the given patent pertains to the educational process. A patent is considered to fall within the educational sphere if its description mentions, for example:
- situations in which a teacher (educator) instructs students,
- the use of pedagogical methods or educational technologies,
- the application of devices or methods for the transmission of knowledge and professional development.
If at least one of these, or a semantically similar, element appears in the description, return True; otherwise, return False.

Format your answer strictly as a JSON structure of the following form:

{{
  "teaching_content": true
}}

or

{{
  "teaching_content": false
}}

Here is the text: {text_safe}
"""


    retry_limit = 3
    for attempt in range(1, retry_limit + 1):
        try:
            async with semaphore:
                async with limiter:
                    response = await client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        web_search=False,
                    )
            if response and response.choices and response.choices[0].message:
                content = response.choices[0].message.content
                # Clean up the response content to remove markdown formatting.
                content_cleaned = extract_json(content)
                try:
                    parsed = json.loads(content_cleaned)
                    if "teaching_content" in parsed:
                        return parsed["teaching_content"]
                    else:
                        logger.error(f"Key 'teaching_content' not found in response JSON: {parsed}")
                        return False
                except Exception as e:
                    logger.error(f"Failed to parse JSON from cleaned API response: {content_cleaned}, error: {e}")
                    return False
            else:
                logger.error("Unexpected response format from API.")
                return False
        except Exception as e:
            logger.error(f"Error calling API on attempt {attempt}: {e}")
            if attempt == retry_limit:
                return False
            await asyncio.sleep(attempt)
    return False

# ------------------------------------------------------------------------------
# Processing a Single Patent Record
# ------------------------------------------------------------------------------
async def process_patent(client: AsyncClient, record: dict, semaphore: asyncio.Semaphore, limiter: AsyncLimiter):
    """
    Processes a single patent record. If a non-empty 'abstract_text' is present,
    it calls the g4f API and adds a new key 'teaching_content' with the Boolean result.
    """
    if shutdown_requested:
        return

    # Use "abstract_text" from the JSON structure (instead of 'abstract')
    abstract = record.get("abstract_text", "").strip()
    if abstract:
        teaching_value = await async_get_teaching_content(client, abstract, semaphore, limiter)
        record["teaching_content"] = teaching_value
    else:
        record["teaching_content"] = None

# ------------------------------------------------------------------------------
# Main Async Entry Point
# ------------------------------------------------------------------------------
async def main():
    global shutdown_requested

    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop)

    # Verify that the input directory exists.
    input_path = Path(INPUT_DIR)
    if not input_path.exists() or not input_path.is_dir():
        logger.error(f"Input directory '{INPUT_DIR}' does not exist or is not a directory.")
        return

    # Find all JSON files in the selected folder and its subfolders.
    json_files = list(input_path.rglob("*.json"))
    if not json_files:
        logger.error(f"No JSON files found in the directory '{INPUT_DIR}' and its subfolders.")
        return
    logger.info(f"Found {len(json_files)} JSON file(s) for processing.")

    # Read and combine records from all JSON files.
    records = []
    for file in json_files:
        try:
            data = await async_read_json(file)
            if isinstance(data, list):
                records.extend(data)
                logger.info(f"Loaded {len(data)} records from {file}")
            else:
                logger.error(f"File {file} does not contain a list of records.")
        except Exception as e:
            logger.error(f"Error reading file {file}: {e}")

    logger.info(f"Total records loaded: {len(records)}")

    # Filter records with a non-empty 'abstract_text'
    records = [record for record in records if record.get("abstract_text", "").strip()]
    logger.info(f"Found {len(records)} records with non-empty 'abstract_text' for processing.")

    # Initialize the g4f API client, semaphore, and rate limiter.
    client = AsyncClient()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    limiter = AsyncLimiter(max_rate=RATE_LIMIT, time_period=RATE_PERIOD)

    # Process each patent record concurrently.
    tasks = [
        asyncio.create_task(process_patent(client, record, semaphore, limiter))
        for record in records
    ]
    
    # Process tasks with progress feedback.
    for task in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Processing records", unit="record"):
        try:
            await task
        except Exception as e:
            logger.error(f"Error processing record: {e}")

    # Select only items where "teaching_content" is True.
    filtered_records = [record for record in records if record.get("teaching_content") is True]
    logger.info(f"{len(filtered_records)} records have teaching content.")

    # Determine the output file name using the selected folder name.
    selected_folder_name = os.path.basename(os.path.normpath(INPUT_DIR))
    output_file = os.path.join(os.getcwd(), f"{selected_folder_name}_filtered.json")
    try:
        await async_write_text(output_file, json.dumps(filtered_records, ensure_ascii=False, indent=2))
        logger.info(f"Saved filtered data to '{output_file}'")
    except Exception as e:
        logger.error(f"Error saving filtered file: {e}")

    # Gracefully close the client if a close or aclose method is available.
    if hasattr(client, "close"):
        try:
            await client.close()
        except Exception as e:
            logger.error(f"Error closing client: {e}")
    elif hasattr(client, "aclose"):
        try:
            await client.aclose()
        except Exception as e:
            logger.error(f"Error closing client: {e}")

# ------------------------------------------------------------------------------
# Main Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Operation interrupted by user.")
