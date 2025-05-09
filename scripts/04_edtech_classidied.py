import asyncio
import json
import logging
import re
import signal
from pathlib import Path

import nest_asyncio
from tqdm.asyncio import tqdm
from aiolimiter import AsyncLimiter

from g4f.client import AsyncClient

# Patch nested event loops (useful in interactive environments)
nest_asyncio.apply()

# ------------------------------------------------------------------------------
# Configuration and Constants
# ------------------------------------------------------------------------------
INPUT_FILE = "patents_with_description.json"   # Input file name
OUTPUT_FILE = "edtech_classified.json"           # New output file name

MAX_CONCURRENT_REQUESTS = 20   # Maximum concurrent API calls
RATE_LIMIT = 50                # Maximum requests per RATE_PERIOD seconds
RATE_PERIOD = 1

# Global shutdown flag.
shutdown_requested = False

# ------------------------------------------------------------------------------
# Updated Taxonomy Prompt Template (fixed with doubled curly braces for literal JSON)
# ------------------------------------------------------------------------------
EDTECH_CLASSIFICATION_PROMPT_TEMPLATE = """
Analyze the patent text provided below and classify the described educational technology according to the following taxonomy. Return a JSON response following the structure specified.

**Classification Taxonomy:**

1. Student Engagement and Motivation Technologies (code: "engagement")
   - Aim: Ensure active student participation through gamification, virtual rewards, and interactive platforms.
   - Research: Studies indicate that gamification improves evaluations [Rincon-Flores et al, 2021] and platforms like Kahoot! and Quizizz enhance active learning [Parra et al, 2021].

2. Access and Digital Equality Technologies (code: "access")
   - Aim: Bridge the digital divide by enabling low-bandwidth, offline-capable web applications and adaptive interfaces.
   - Research: The pandemic revealed access issues in rural and low-income areas [Isaeva, 2024; Cabaleiro-Cerviño et al, 2020].

3. Hybrid and Flexible Learning Technologies (code: "hybrid")
   - Aim: Integrate in-person and online learning components through hybrid platforms that manage mixed groups.
   - Research: Combining LMS with digital collaboration tools, such as VR tours, improves mixed audience instruction [Globa, 2022; Ingabire et al, 2024].

4. AI Technologies for Assessment and Learning Analytics (code: "ai_assessment")
   - Aim: Employ AI and machine learning for unbiased assessment, automated grading, proctoring, and comprehensive learning analytics.
   - Research: AI-driven evaluations address grade inflation and enhance test integrity [Owoc et al, 2021; Abubakar et al, 2024; Alishev et al, 2022].

5. Teacher Support and Professional Development Technologies (code: "teacher_support")
   - Aim: Assist educators in adapting to remote and hybrid teaching via automation, AI modules, and specialized professional development platforms.
   - Research: Dedicated digital platforms enhance teacher competency and efficiency in remote environments [Gondwe, 2021].

**Response Requirements:**
1. Analyze the provided patent text.
2. Identify its key technological features and determine the appropriate taxonomy code.
3. Return a JSON response with the following structure:
{{
  "technology_class": "<compact code>",
  "reason": "<brief justification>"
}}

If uncertain about the classification, return:
{{
  "technology_class": "Uncertain",
  "reason": "<brief justification>"
}}

Provide your response in valid JSON format without additional commentary.

**Patent Text for Analysis:**
{text}
"""

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
# Helper Function: Extract JSON from text (e.g., markdown-wrapped or incomplete JSON)
# ------------------------------------------------------------------------------
def extract_json(text: str) -> str:
    """
    Attempts to extract a valid JSON substring from a text response that may include
    markdown formatting or be missing the outer braces.
    """
    text = text.strip()

    # First: try to extract JSON from within triple backticks
    markdown_pattern = r"```(?:json)?\s*(\{.*\})\s*```"
    match = re.search(markdown_pattern, text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
    else:
        # Otherwise, assume the entire text is candidate JSON.
        candidate = text

    # If the candidate does not start/end with curly braces, try to fix it.
    if not (candidate.startswith("{") and candidate.endswith("}")):
        candidate = candidate.strip().strip(',')
        candidate = "{" + candidate + "}"
    
    # Validate candidate by attempting to load it.
    try:
        obj = json.loads(candidate)
        # Re-dump to normalize the JSON string.
        return json.dumps(obj)
    except json.JSONDecodeError as e:
        logger.error(f"extract_json: failed to decode candidate: {candidate}")
        raise e

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
# Asynchronous Function: Get EdTech Classification via API
# ------------------------------------------------------------------------------
async def async_get_edtech_classification(client: AsyncClient, text, semaphore: asyncio.Semaphore, limiter: AsyncLimiter):
    """
    Uses the g4f model to classify the patent description according to the provided
    EdTech taxonomy. Expects a JSON response with 'technology_class' and 'reason'.
    Returns a dictionary with these keys or default values on failure.
    """
    default_result = {"technology_class": "Unknown", "reason": "No description provided"}
    error_result = {"technology_class": "Error", "reason": "API call failed"}

    # Ensure description is a string.
    if isinstance(text, list):
        text = "\n\n".join(str(part) for part in text)
    elif not isinstance(text, str):
        text = str(text)

    if not text.strip():
        logger.warning("Received empty description for classification.")
        return default_result  # Return default if description is empty

    # Escape literal curly braces in 'text' for safe .format() substitution.
    text_safe = text.replace("{", "{{").replace("}", "}}")

    # Format prompt with the provided text.
    prompt = EDTECH_CLASSIFICATION_PROMPT_TEMPLATE.format(text=text_safe)

    retry_limit = 3
    for attempt in range(1, retry_limit + 1):
        if shutdown_requested:
            return error_result  # Stop if shutdown is requested

        try:
            async with semaphore:
                async with limiter:
                    response = await client.chat.completions.create(
                        model="gpt-4o",  # Choose your desired model.
                        messages=[{"role": "user", "content": prompt}],
                        web_search=False,
                    )
            if response and response.choices and response.choices[0].message:
                content = response.choices[0].message.content
                try:
                    content_cleaned = extract_json(content)  # Extract potential JSON.
                    parsed = json.loads(content_cleaned)
                    if not isinstance(parsed, dict):
                        logger.error(f"API response is not a JSON dictionary as expected, got {type(parsed).__name__}: {parsed}")
                        raise ValueError("Invalid JSON structure")
                    # Check for expected keys.
                    if "technology_class" in parsed and "reason" in parsed:
                        return {
                            "technology_class": parsed.get("technology_class"),
                            "reason": parsed.get("reason")
                        }
                    else:
                        logger.error(f"Expected keys not found in response JSON: {parsed}")
                        return {
                            "technology_class": parsed.get("technology_class", "Missing"),
                            "reason": parsed.get("reason", "Missing")
                        }
                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(f"Attempt {attempt}: Failed to parse JSON from API response. Response content:\n{content}\nError: {e}")
                except Exception as e:
                    logger.exception(f"Attempt {attempt}: Unexpected error parsing response: {e}")
            else:
                logger.error(f"Attempt {attempt}: Unexpected or empty API response format.")
        except Exception as e:
            logger.exception(f"Attempt {attempt}: Error calling API: {e}")

        # Exponential backoff before retrying.
        if attempt < retry_limit:
            await asyncio.sleep(attempt * 2)

    logger.error(f"Failed to get valid classification after {retry_limit} attempts. Text (first 100 chars): {text[:100]}...")
    return error_result

# ------------------------------------------------------------------------------
# Process a Single Patent Record
# ------------------------------------------------------------------------------
async def process_patent(client: AsyncClient, record: dict, semaphore: asyncio.Semaphore, limiter: AsyncLimiter):
    """
    Processes a single patent record by classifying its 'description' using the g4f API.
    On error, the record is updated with an error classification.
    """
    try:
        if shutdown_requested:
            record["technology_class"] = "Shutdown"
            record["reason"] = "Shutdown requested"
            return

        description = record.get("description", "").strip()
        default_classification = {"technology_class": "No Description", "reason": "No description provided"}

        if description:
            classification_result = await async_get_edtech_classification(client, description, semaphore, limiter)
            record["technology_class"] = classification_result.get("technology_class", "Error")
            record["reason"] = classification_result.get("reason", "Error")
        else:
            record["technology_class"] = default_classification["technology_class"]
            record["reason"] = default_classification["reason"]

    except Exception as e:
        logger.exception(f"Error processing patent record (ID: {record.get('id', 'unknown')}): {e}")
        record["technology_class"] = "Error"
        record["reason"] = "Exception during processing"

# ------------------------------------------------------------------------------
# Main Async Entry Point
# ------------------------------------------------------------------------------
async def main():
    global shutdown_requested

    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop)

    input_path = Path(INPUT_FILE)
    if not input_path.exists():
        logger.error(f"Input file '{INPUT_FILE}' does not exist.")
        return

    try:
        data = await async_read_json(input_path)
        if isinstance(data, list):
            records = data
        else:
            logger.error("The input JSON does not contain a list of records.")
            return
        logger.info(f"Loaded {len(records)} records from {INPUT_FILE}")
    except Exception as e:
        logger.exception(f"Error reading input file: {e}")
        return

    # Initialize g4f API client, semaphore, and rate limiter.
    client = AsyncClient()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    limiter = AsyncLimiter(max_rate=RATE_LIMIT, time_period=RATE_PERIOD)

    tasks = [
        asyncio.create_task(process_patent(client, record, semaphore, limiter))
        for record in records
        if not shutdown_requested
    ]

    processed_count = 0

    for future in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Classifying patents", unit="patent"):
        if shutdown_requested:
            for task in tasks:
                if not task.done():
                    task.cancel()
            break
        try:
            await future
            processed_count += 1
        except asyncio.CancelledError:
            logger.warning("A task was cancelled due to shutdown request.")
        except Exception as e:
            logger.error(f"Error processing a record task: {e}")

    logger.info(f"Processed {processed_count} records.")
    if shutdown_requested:
        logger.warning("Processing was interrupted by a shutdown request. Output may be incomplete.")

    try:
        await async_write_text(OUTPUT_FILE, json.dumps(records, ensure_ascii=False, indent=2))
        logger.info(f"Saved processed data to '{OUTPUT_FILE}'")
    except Exception as e:
        logger.exception(f"Error writing output file '{OUTPUT_FILE}': {e}")

    # Gracefully close the client.
    if hasattr(client, "aclose"):
        try:
            await client.aclose()
            logger.info("Closed API client.")
        except Exception as e:
            logger.exception(f"Error closing client (aclose): {e}")
    elif hasattr(client, "close"):
        try:
            client.close()
        except Exception as e:
            logger.exception(f"Error closing client (close): {e}")

# ------------------------------------------------------------------------------
# Main Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Operation interrupted by user (KeyboardInterrupt).")
    except asyncio.CancelledError:
        logger.info("Main task cancelled.")
    finally:
        logging.shutdown()
