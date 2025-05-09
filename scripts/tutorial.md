## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Overview of the Pipeline](#overview)
3. [Step 1: Prepare Your Environment and CSV Files](#step-1)
4. [Step 2: Run the Initial Patent Scraping Script](#step-2)
5. [Step 3: Filter Patents for Teaching Content](#step-3)
6. [Step 4: Enrich Patent Data with Detailed Scraping](#step-4)
7. [Step 5: Classify Patents Using the EdTech Taxonomy](#step-5)
8. [Step 6: Check for COVID-Related Patent Descriptions](#step-6)
9. [Step 7: Final Dataset and Next Steps](#step-7)
10. [Additional Notes and Adjustments](#notes)

---

## Prerequisites

Before beginning, ensure that you have the following:

- **Python Environment:** Python 3.8 or later is recommended. Setting up a virtual environment is advised.
- **Required Packages:**  
  Install all necessary Python dependencies. For example, you may need:
  ```bash
  pip install pandas tqdm lxml beautifulsoup4 requests nest_asyncio aiolimiter g4f
  ```
  > **Note:** The package `google_patent_scraper` is referenced by the script. Make sure it is installed or available on your PYTHONPATH.
- **Internet Connection:** The scripts use online scraping and API calls (via the g4f client and GPT‑4 API) so a stable connection is required.
- **CSV Files:** Your CSV files containing basic patent search results should reside in the `patents_csvs` directory. They must match the filename pattern `gp-search-20*.csv` and include relevant columns such as `"id"`, `"result link"`, `"title"`, etc.

---

## Overview of the Pipeline

The pipeline consists of several stages:

1. **Initial Patent Scraping:**  
   *The first script* reads CSV files from `patents_csvs`, deduplicates them by patent ID, checks against already processed patents (stored in the `json_output` folder), and uses the `google_patent_scraper` to scrape new patent details from Google Patents. It outputs the results as chunked JSON files.

2. **Teaching Content Filter:**  
   An **asynchronous script** reads the JSON files from the `json_output` folder, processes each record’s abstract (or a similar field) by sending it to a GPT‑4 API (using the g4f client), and determines if the patent is related to the educational (teaching) process. It saves only those records with a positive response (i.e., `"teaching_content": true).

3. **Data Enrichment via Improved Scraper:**  
   Another script further scrapes each filtered patent from Google Patents (adding metadata, classifications, detailed abstract, description, claims, and citations) using BeautifulSoup and an improved scraper class. It merges the scraped data with the original record.

4. **EdTech Classification:**  
   An asynchronous classification script uses a custom taxonomy prompt (via the g4f client) to classify each patent’s description into one of several predefined educational technology categories. The result (including a compact taxonomy code and a brief justification) is added to the record.

5. **COVID‑Related Check:**  
   Finally, one more asynchronous script examines each patent record to determine if the teaching/learning method was developed or used in response to the COVID‑19 pandemic. A GPT‑4 API call returns a JSON with a key `"is_covid"` (either `"covid"` or `"non-covid"`), which is added to the record.

Each stage outputs its result to a JSON file, creating a chained dataset that is enriched and classified.

---

## Step 1: Prepare Your Environment and CSV Files <a name="step-1"></a>

1. **Set Up Your Virtual Environment (Optional but Recommended):**
   ```bash
   python -m venv venv
   source venv/bin/activate      # Linux/macOS
   venv\Scripts\activate         # Windows
   ```

2. **Install Dependencies:**
   ```bash
   pip install pandas tqdm lxml beautifulsoup4 requests nest_asyncio aiolimiter g4f
   # Also, ensure any custom module like google_patent_scraper is available.
   ```

3. **Organize CSV Files:**
   - Ensure that all CSV files (with names matching `gp-search-20*.csv`) are placed under the folder `patents_csvs`.
   - Verify that the CSV files include columns such as `"id"`, `"result link"`, and `"title"`, and optionally `"abstract_text"` if available.

---

## Step 2: Run the Initial Patent Scraping Script <a name="step-2"></a>

This script:

- **Scans:** Recursively reads CSV files from `patents_csvs`.
- **Deduplicates:** Uses a normalized patent ID (from the `"id"` column or extracted from the `"result link"`).
- **Scrapes:** For each new patent (not already processed in the `json_output` folder), it uses `google_patent_scraper` to scrape the patent page.
- **Outputs:** Saves the scraped patent data into chunked JSON files in `patents_csvs/json_output` (e.g., `all_patents_000.json`, `all_patents_001.json`, etc.).

### How to Run:

1. Save the first script as, for example, `scrape_patents.py`.
2. Run it from the command line:
   ```bash
   python scrape_patents.py
   ```
3. Monitor the console logs for progress and any retry/error messages.

Once completed, you should find the JSON files in the folder:
```
patents_csvs/json_output/
```

---

## Step 3: Filter Patents for Teaching Content <a name="step-3"></a>

This asynchronous script performs the following:

- **Loads:** Reads all JSON files from `patents_csvs/json_output`.
- **Processes:** For each patent record with a non-empty abstract (or a similar text field), it makes an API call via the g4f (GPT‑4) client to check whether the content pertains to an educational (teaching) process.
- **Annotates & Filters:** Adds a new boolean field `"teaching_content"` (either `true` or `false`) to each record, then retains only patents marked as teaching content.
- **Saves:** Writes the filtered results to an output JSON file. The file name is constructed using the input folder name (for example, if the folder name is `json_output`, the output might be `json_output_filtered.json`).

### How to Run:

1. Save the second script (the asynchronous teaching content check) as, for example, `teaching_filter.py`.
2. Run it:
   ```bash
   python teaching_filter.py
   ```
3. Once finished, check for the filtered output file (e.g., `json_output_filtered.json`) in your working directory.

---

## Step 4: Enrich Patent Data with Detailed Scraping <a name="step-4"></a>

This upgraded scraping pipeline script:

- **Reads:** Uses the filtered JSON output (e.g., `json_output_filtered.json`) as input.
- **Scrapes Additional Data:** Visits each patent’s Google Patents URL using a more robust scraper built on `requests` and `BeautifulSoup` with proper error handling and retries.
- **Parses:** Extracts extended metadata (title, publication date, inventor names), classifications, abstract, description, claims, and citations.
- **Merges:** Combines the newly scraped details with the original record.
- **Writes:** Outputs the enriched records to a new JSON file (for example, `patents_with_description.json`).

### How to Run:

1. Save the improved scraping script as, for example, `improved_scraper.py`.
2. Run the script:
   ```bash
   python improved_scraper.py
   ```
3. Verify that the output file `patents_with_description.json` is created.

---

## Step 5: Classify Patents Using the EdTech Taxonomy <a name="step-5"></a>

This asynchronous classification script:

- **Loads:** Reads `patents_with_description.json`.
- **Sends:** For each patent record, it sends a prompt (with appropriate explanations of the EdTech taxonomy) to the g4f API.
- **Receives:** The API is expected to return a JSON with keys `"technology_class"` and `"reason"`.
- **Annotates:** Each record is updated with these classification fields.
- **Writes:** The classified records are saved to an output file named (for instance) `edtech_classified.json`.

### How to Run:

1. Save the classification script as (for example) `edtech_classification.py`.
2. Run the script:
   ```bash
   python edtech_classification.py
   ```
3. Check that the file `edtech_classified.json` is generated with the additional classification data.

---

## Step 6: Check for COVID‑Related Patent Descriptions <a name="step-6"></a>

The final asynchronous script in the pipeline:

- **Loads:** Reads the classified data from `edtech_classified.json`.
- **Processes:** It uses another GPT‑4 API call (via g4f) with a prompt specifically asking whether the patent’s description indicates that the technology/method was developed or employed in response to the COVID‑19 pandemic.
- **Annotates:** Each record is updated with a new key `"is_covid"` whose value will be either `"covid"` or `"non-covid"`.
- **Writes:** The final enriched and annotated dataset is saved to the output file (for example, `descriptions_covid_check.json`).

### How to Run:

1. Save the COVID check script as, for instance, `covid_check.py`.
2. Execute it:
   ```bash
   python covid_check.py
   ```
3. After the script finishes, verify that the output file `descriptions_covid_check.json` contains the updated patent records.

---

## Step 7: Final Dataset and Next Steps <a name="step-7"></a>

After completing all steps, your final dataset is available in the output file (e.g., `descriptions_covid_check.json`). This file contains patent records that have been:

- Scraped from CSV sources.
- De-duplicated.
- Filtered by teaching (educational) content.
- Enriched with additional metadata and details from Google Patents.
- Classified according to an EdTech taxonomy.
- Checked for COVID‑related characteristics.

At this point, you can use this JSON dataset for further analysis, visualization, or integration into your research/workflows.

---

## Additional Notes and Adjustments <a name="notes"></a>

- **Logging and Debugging:**  
  Each script uses the Python `logging` module (and sometimes file logging) to output progress, errors, and debug information. Monitor the console (or log files such as `patent_scraper.log`) for any issues. Adjust the logging level if needed.

- **Rate Limits and Concurrency Controls:**  
  The asynchronous scripts use rate limiting (via `AsyncLimiter`) and control concurrency through semaphores (e.g., `MAX_CONCURRENT_REQUESTS`). You may adjust these parameters (such as `RATE_LIMIT`, `RATE_PERIOD`, and the number of concurrent tasks) in the configuration sections at the top of each script if you experience API errors or need to reduce load.

- **Script Configuration:**  
  - Make sure that the configuration constants (e.g., `INPUT_DIR`, `OUTPUT_FOLDER`, `CHUNK_SIZE`, etc.) match your local setup.
  - The API model names (e.g., `"gpt-4o"`) and other parameters should be verified for compatibility with your g4f client and your API subscription.

- **Dependencies:**  
  Ensure that all third-party dependencies (especially any custom modules like `google_patent_scraper`) are properly installed and accessible in your Python path.

- **Graceful Shutdown:**  
  The asynchronous scripts include signal handling (for SIGINT/SIGTERM) to allow graceful shutdown if you need to abort processing.

By following these detailed steps, you will be able to generate and refine a dataset of educational patent documents using the provided scripts. Feel free to modify prompts, logging, or concurrency settings to suit your specific use case or environment.
