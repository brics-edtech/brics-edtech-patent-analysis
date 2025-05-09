# brics-edtech-patent-analysis
paper's repository

Dataset: Data Collection, Processing, and Annotation

In this section, we describe the methodology used to create the research dataset, including data sources, processing steps, and annotation by a large language model.

---

### 2.1.1. Source and Data Collection

The primary data source for this study was the patents.google.com database. This platform was chosen for its extensive collection of full-text national and international patent documents, providing broad global coverage of patent literature. National documents include patent applications filed with individual national patent offices, whereas international documents refer to applications submitted under the Patent Cooperation Treaty (PCT) to the World Intellectual Property Organization (WIPO). These international filings signal an intent to seek patent protection in multiple jurisdictions and often represent inventions with significant commercial potential aimed at global deployment.

Advanced search on Google Patents enabled filtering by specific criteria and crafting complex queries with Boolean operators. To overcome the service limitation of displaying a maximum of 25,000 patents per query and to maximize data retrieval, we used semantically broad search terms typically associated with educational technologies: “education,” “teaching,” and “learning.” A patent document was included in the search results if any of these terms appeared in its title, abstract, full description, or claims.

To analyze the geographical distribution of patents, we leveraged the platform’s ability to filter by national patent office codes (two-letter country codes, e.g., RU for Russia, BR for Brazil, CN for China). This approach yielded data from 91 patent offices, ensuring truly global coverage of educational patents.

For a comparative analysis of patenting activity before and after the COVID-19 pandemic, we selected two equal time periods. The first spans from January 1, 2015 to January 1, 2020, corresponding to the era of gradual digitalization in education. The second covers January 1, 2020 to January 1, 2025, encompassing the pandemic and its aftermath. This division allows us to compare the representation of our chosen technology classes across two distinct phases, examine correlations with the pandemic period, and quantitatively assess observed trends.

Patent metadata were exported as CSV files, containing the following fields for each patent:

| No. | Field                      | Description                                                                                   |
| --- | -------------------------- | --------------------------------------------------------------------------------------------- |
| 1   | id                         | Unique patent or application identifier                                                       |
| 2   | title                      | Patent or application title                                                                   |
| 3   | assignee                   | Patent holder (company, university, or individual)                                            |
| 4   | inventor/author            | Inventor(s) or author(s)                                                                      |
| 5   | priority date              | Earliest filing date in any patent office (critical for priority determination)               |
| 6   | filing/creation date       | Filing date in the specific patent office                                                     |
| 7   | publication date           | Publication date of the patent or application                                                 |
| 8   | grant date                 | Grant date of the patent (if approved)                                                        |
| 9   | result link                | Link to full patent text on Google Patents                                                    |
| 10  | representative figure link | Link to key figure or diagram from the patent (e.g., chemical structure or technical drawing) |

To capture the earliest filing information—which is especially important for rapid pandemic-era responses—we used the “priority date” field as our primary timestamp. This date reflects the first-ever filing in any patent office worldwide, enabling early detection of shifts in the technological landscape.

Data extraction from Google Patents was performed manually using the “Download” function to save CSV files locally.

---

Data Processing and Annotation

Since the raw CSV files lacked sufficient detail for in-depth analysis, we implemented an automated enrichment pipeline using Python scripts. The full codebase and resulting data are available in the current project repository. The following subsections outline the main processing stages and their objectives.

#### Abstract Collection (get_patents.py)

First, we built a database of patent abstracts. From each CSV export, we extracted unique patent IDs. Using a specialized library, we retrieved for each ID the patent title, author information, publication and priority dates, citation counts, and the abstract text. The output was a set of JSON files, each containing detailed metadata and the abstract for one patent—forming the raw abstracts database that underpins trend analysis in early-stage inventive activity.

#### Relevance Filtering for Educational Technology (get_edtech.py)

Next, we filtered the collected abstracts to isolate those directly related to educational technologies. We employed the GPT-4o model, providing it with a prompt to examine each abstract and assign a Boolean flag `teaching_content` indicating relevance. This step yielded a subset of patents mapped to five target technology classes (detailed in Sections 1.1.1–1.1.5 of the main report).

#### Detailed Metadata Retrieval (get_description.py)

For each patent passing the relevance filter, we fetched the full Google Patents page. A custom parser extracted and saved metadata such as: title, inventor details, dates, CPC classification codes, abstract text, full description, claim language, and citation data. The result was a deeply annotated patent database capturing all necessary details for a comprehensive technology-landscape analysis.

#### EdTech Taxonomy Classification (edtech_classified.py)

Finally, we classified each selected patent according to a predefined taxonomy of five technology classes: “engagement” (1.1.1), “access” (1.1.2), “hybrid” (1.1.3), “ai\_assessment” (1.1.4), and “teacher\_support” (1.1.5). Using GPT-4o, we supplied each patent’s full description along with a template enumerating the class codes and names. The model assigned each patent to one class, producing a structured dataset ready for quantitative and qualitative comparative analysis.

#### (Additionally) Annotation based on the presence of mentions of COVID-10 in the patent description (is_covid.py)

If the description mentions the topic of COVID-19, the patent is labeled as “covid”; otherwise, it is labeled as “non-covid.”

---

### 2.1.3. Final Dataset

The completed pipeline produced a dataset enriched with both our five functional technology classes (1.1.1–1.1.5) and a marker for COVID-19 relevance. This allowed precise filtering of patents according to research criteria.

The final dataset includes the following fields per patent:

| No. | Field                        | Description                                                                    |
| --- | ---------------------------- | ------------------------------------------------------------------------------ |
| 1   | id                           | Unique patent or application identifier                                        |
| 2   | title                        | Patent or application title                                                    |
| 3   | inventors                    | Array of inventors’ names                                                      |
| 4   | assignee                     | Patent holder                                                                  |
| 5   | application\_number          | Application or patent number                                                   |
| 6   | pub\_date                    | Publication date                                                               |
| 7   | priority\_date               | Priority date                                                                  |
| 8   | grant\_date                  | Grant date                                                                     |
| 9   | filing\_date                 | Filing date                                                                    |
| 10  | url                          | Link to the online patent page                                                 |
| 11  | abstract                     | Patent abstract                                                                |
| 12  | description                  | Full technical and conceptual description                                      |
| 13  | classification\_numbers      | Array of CPC classification codes                                              |
| 14  | classification\_descriptions | Array of descriptions explaining the classification codes                      |
| 15  | teaching\_content            | Boolean flag for educational-technology relevance                              |
| 16  | forward\_citations           | List of patents citing this patent                                             |
| 17  | backward\_citations          | List of patents cited by this patent                                           |
| 18  | processing\_time             | Data processing time in the system                                             |
| 19  | technology\_class            | Assigned EdTech taxonomy class                                                 |
| 20  | reason                       | Rationale for classification decisions (e.g., why excluded from a given class) |
| 21  | is\_covid                    | Boolean marker for COVID-19 relevance                                          |



