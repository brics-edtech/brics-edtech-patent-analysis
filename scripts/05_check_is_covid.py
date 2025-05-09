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
INPUT_FILE = "edtech_classified.json"  # input file name
OUTPUT_FILE = "descriptions_covid_check.json"   # output file name

MAX_CONCURRENT_REQUESTS = 20   # Maximum concurrent API calls
RATE_LIMIT = 50                # Maximum requests per RATE_PERIOD seconds
RATE_PERIOD = 1

# Global shutdown flag.
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
# Asynchronous Function to Call g4f Model for Covid-19 Educational Check
# ------------------------------------------------------------------------------
async def async_get_covid_status(client: AsyncClient, text, semaphore: asyncio.Semaphore, limiter: AsyncLimiter):
    """
    Uses the g4f model to determine if the provided description text indicates
    that the technology or method for teaching/learning was developed or used as a response
    to the Covid-19 pandemic. The expected answer is a JSON structure with a key "is_covid"
    whose value is either "covid" or "non-covid".
    """
    # Ensure text is a string.
    if isinstance(text, list):
        text = "\n\n".join(str(part) for part in text)
    elif not isinstance(text, str):
        text = str(text)

    if not text.strip():
        return "non-covid"

    # Escape any literal curly braces in text to prevent f-string formatting issues.
    text_safe = text.replace("{", "{{").replace("}", "}}")

    prompt = f"""
Please analyze the following patent description and determine if it describes a technology or method for teaching or learning that was developed or employed specifically in response to the Covid-19 pandemic.
If the description indicates that the technology or method was developed or used as a response to the Covid-19 pandemic, respond with exactly the following JSON structure:

{{
  "is_covid": "covid"
}}

Otherwise, respond with exactly the following JSON structure:

{{
  "is_covid": "non-covid"
}}

Here is the description:
{text_safe}
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
                    if "is_covid" in parsed:
                        return parsed["is_covid"]
                    else:
                        logger.error(f"Key 'is_covid' not found in response JSON: {parsed}")
                        return "non-covid"
                except Exception as e:
                    logger.error(f"Failed to parse JSON from API response: {content_cleaned}. Error: {e}")
                    return "non-covid"
            else:
                logger.error("Unexpected response format from API.")
                return "non-covid"
        except Exception as e:
            logger.error(f"Error calling API on attempt {attempt}: {e}")
            if attempt == retry_limit:
                return "non-covid"
            await asyncio.sleep(attempt)
    return "non-covid"

# ------------------------------------------------------------------------------
# Processing a Single Patent Record
# ------------------------------------------------------------------------------
async def process_patent(client: AsyncClient, record: dict, semaphore: asyncio.Semaphore, limiter: AsyncLimiter):
    """
    Processes a single patent record. It checks the 'description' field and uses the
    g4f API to determine if the patent is related (via its description) to technology or a method
    for teaching/learning due to the Covid-19 pandemic. It then adds a new key 'is_covid' with the result.
    """
    if shutdown_requested:
        return

    description = record.get("description", "").strip()
    if description:
        covid_value = await async_get_covid_status(client, description, semaphore, limiter)
        record["is_covid"] = covid_value
    else:
        record["is_covid"] = "non-covid"

# ------------------------------------------------------------------------------
# Main Async Entry Point
# ------------------------------------------------------------------------------
async def main():
    global shutdown_requested

    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop)

    # Verify that the input file exists.
    input_path = Path(INPUT_FILE)
    if not input_path.exists():
        logger.error(f"Input file '{INPUT_FILE}' does not exist.")
        return

    # Read records from the input JSON file.
    try:
        data = await async_read_json(input_path)
        if isinstance(data, list):
            records = data
        else:
            logger.error("The input JSON does not contain a list of records.")
            return
        logger.info(f"Loaded {len(records)} records from {INPUT_FILE}")
    except Exception as e:
        logger.error(f"Error reading input file: {e}")
        return

    # Initialize the g4f API client, semaphore, and rate limiter.
    client = AsyncClient()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    limiter = AsyncLimiter(max_rate=RATE_LIMIT, time_period=RATE_PERIOD)

    # Process each patent record concurrently.
    tasks = [
        asyncio.create_task(process_patent(client, record, semaphore, limiter))
        for record in records
    ]
    
    # Process tasks with a progress bar.
    for _ in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Processing records", unit="record"):
        try:
            await _
        except Exception as e:
            logger.error(f"Error processing a record: {e}")

    # Save the updated records (with the new 'is_covid' key) to the output file.
    try:
        await async_write_text(OUTPUT_FILE, json.dumps(records, ensure_ascii=False, indent=2))
        logger.info(f"Saved processed data to '{OUTPUT_FILE}'")
    except Exception as e:
        logger.error(f"Error writing output file: {e}")

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
