"""
Canonical test queries shared across all tests/ scripts.

Sourced from test_query_retrieval.py — these are the exact queries
used for end-to-end, integration, and unit tests.

Import usage:
    from test_queries import TEST_QUERIES, QUERY_EXPECTATIONS
"""

from dataclasses import dataclass, field


@dataclass
class QueryExpectation:
    """Expected results for a single query."""
    query_id: int
    query: str
    expected_policy: str
    expected_file: str
    description: str
    expected_metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Canonical query list (order matters for reporting)
# ---------------------------------------------------------------------------

QUERY_EXPECTATIONS: list[QueryExpectation] = [
    # -- Exact document lookups by policy number --------------------------
    QueryExpectation(
        query_id=1,
        query="Find Policy 51350 on Paid Time Off",
        expected_policy="51350",
        expected_file="51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
        description="Lookup PTO policy by exact number",
        expected_metadata={
            "parent_title": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=2,
        query="Show me Policy 52005 for the Uniform Dress Code",
        expected_policy="52005",
        expected_file="52005 - Operational Matters_ Uniform Dress Code (2583_19).docx",
        description="Lookup Uniform Dress Code by exact policy number",
        expected_metadata={
            "parent_title": "52005 - Operational Matters_ Uniform Dress Code (2583_19).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=3,
        query="Pull up document 87100 about the AI and LLM policy",
        expected_policy="87100",
        expected_file="87100 - Generative Artificial Intelligence (AI) & Large Language Models (LLM) Policy (23653_5).docx",
        description="Lookup AI/LLM policy by document number",
        expected_metadata={
            "parent_title": "87100 - Generative Artificial Intelligence (AI) & Large Language Models (LLM) Policy (23653_5).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=4,
        query="Find the IT Acceptable Use Policy number 81100",
        expected_policy="81100",
        expected_file="81100 - Information Technology Acceptable Use Policy (23666_5).docx",
        description="Lookup IT Acceptable Use by policy number",
        expected_metadata={
            "parent_title": "81100 - Information Technology Acceptable Use Policy (23666_5).docx",
            "container": "ask-hr-knowledge",
        },
    ),

    # -- Topic / title lookups (no policy number in the query) ------------
    QueryExpectation(
        query_id=5,
        query="Show me the Short-Term Disability policy",
        expected_policy="51370",
        expected_file="51370 - Short-Term Disability (23317_1).docx",
        description="Lookup Short-Term Disability by title only",
        expected_metadata={
            "parent_title": "51370 - Short-Term Disability (23317_1).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=6,
        query="Find the SOP for Uniform Issuance",
        expected_policy="",
        expected_file="SOP - Uniform Issuance (23686_3).docx",
        description="Lookup SOP Uniform Issuance by title (no policy number)",
        expected_metadata={
            "parent_title": "SOP - Uniform Issuance (23686_3).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=7,
        query="What does the Emergency Notification System policy say?",
        expected_policy="83400",
        expected_file="83400 - Emergency Notification System Policy (23234_0).doc",
        description="Lookup Emergency Notification by title",
        expected_metadata={
            "parent_title": "83400 - Emergency Notification System Policy (23234_0).doc",
            "container": "ask-hr-knowledge",
        },
    ),

    # -- Natural language / broad topic lookups ---------------------------
    QueryExpectation(
        query_id=8,
        query="What is the PTO policy?",
        expected_policy="51350",
        expected_file="51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
        description="Natural language PTO question",
        expected_metadata={
            "parent_title": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=9,
        query="What is the holiday pay policy?",
        expected_policy="50715",
        expected_file="50715 - Hours Worked and Pay Administration_ Holiday Pay (23641_4).docx",
        description="Natural language holiday pay question",
        expected_metadata={
            "parent_title": "50715 - Hours Worked and Pay Administration_ Holiday Pay (23641_4).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=10,
        query="What is the Code of Ethics?",
        expected_policy="31000",
        expected_file="31000 - Code of Ethics and Related Matters (9081_12).docx",
        description="Natural language ethics question",
        expected_metadata={
            "parent_title": "31000 - Code of Ethics and Related Matters (9081_12).docx",
            "container": "ask-hr-knowledge",
        },
    ),

    # -- Cross-reference / policy number + topic -------------------------
    QueryExpectation(
        query_id=11,
        query="Show me the probationary period requirements from Policy 50455",
        expected_policy="50455",
        expected_file="50455 - Hiring_ Probationary Period (23389_3).docx",
        description="Lookup Probationary Period by number + topic",
        expected_metadata={
            "parent_title": "50455 - Hiring_ Probationary Period (23389_3).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=12,
        query="Find the Blood Borne Pathogens compliance document 101205",
        expected_policy="101205",
        expected_file="101205 - Blood Borne Pathogens Methods of Compliance (374_0).doc",
        description="Lookup BBP Methods of Compliance by document number",
        expected_metadata={
            "parent_title": "101205 - Blood Borne Pathogens Methods of Compliance (374_0).doc",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=13,
        query="Show me the Computer Replacement Policy 84100",
        expected_policy="84100",
        expected_file="84100 - Computer Replacement Policy (3261_3).doc",
        description="Lookup Computer Replacement by exact policy number",
        expected_metadata={
            "parent_title": "84100 - Computer Replacement Policy (3261_3).doc",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=14,
        query="What are the pre-employment medical examination requirements?",
        expected_policy="50410",
        expected_file="50410 - Hiring_ Pre-employment Medical Examinations (23290_2).docx",
        description="Lookup Pre-employment Medical Exams by topic",
        expected_metadata={
            "parent_title": "50410 - Hiring_ Pre-employment Medical Examinations (23290_2).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=15,
        query="Find Policy 88100 on Mobile Device use",
        expected_policy="88100",
        expected_file="88100 - Mobile Device and Use Policy (23693_3).docx",
        description="Lookup Mobile Device Policy by number",
        expected_metadata={
            "parent_title": "88100 - Mobile Device and Use Policy (23693_3).docx",
            "container": "ask-hr-knowledge",
        },
    ),

    # -- Disambiguation queries -------------------------------------------
    QueryExpectation(
        query_id=16,
        query="What is the PTO accrual rate for part-time employees?",
        expected_policy="51355",
        expected_file="51355 - Types of Leave_ Paid Time Off (PTO) - Part-time (23315_2).docx",
        description="Part-time PTO – should find 51355 not 51350",
        expected_metadata={
            "parent_title": "51355 - Types of Leave_ Paid Time Off (PTO) - Part-time (23315_2).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=17,
        query="Show me the Non-Uniform Dress Code policy",
        expected_policy="52010",
        expected_file="52010 - Operational Matters_ Non-Uniform Dress Code (23685_1).docx",
        description="Non-Uniform dress code – should find 52010 not 52005",
        expected_metadata={
            "parent_title": "52010 - Operational Matters_ Non-Uniform Dress Code (23685_1).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=18,
        query="Find the IT Information Security Policy 83100",
        expected_policy="83100",
        expected_file="83100 - IT Information Security Policy (23806_3).docx",
        description="IT Security vs IT Acceptable Use – should find 83100 not 81100",
        expected_metadata={
            "parent_title": "83100 - IT Information Security Policy (23806_3).docx",
            "container": "ask-hr-knowledge",
        },
    ),

    # -- Career path lookups -----------------------------------------------
    QueryExpectation(
        query_id=19,
        query="What is the career path for an HR Generalist?",
        expected_policy="50815",
        expected_file="50815 - Career Path_ HR Generalist (19791_3).docx",
        description="Career path lookup by role title",
        expected_metadata={
            "parent_title": "50815 - Career Path_ HR Generalist (19791_3).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=20,
        query="Show me the Data Management career path document 50855",
        expected_policy="50855",
        expected_file="50855 - Career Path_ Data Management (DM) (18777_6).docx",
        description="Career path DM by number + title",
        expected_metadata={
            "parent_title": "50855 - Career Path_ Data Management (DM) (18777_6).docx",
            "container": "ask-hr-knowledge",
        },
    ),

    # -- File location queries (where is / give me the link) ---------------
    QueryExpectation(
        query_id=21,
        query="Where is the PTO policy document stored?",
        expected_policy="51350",
        expected_file="51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
        description="Where-is query for PTO policy",
        expected_metadata={
            "parent_title": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=22,
        query="Where is Policy 52005 for the Uniform Dress Code located?",
        expected_policy="52005",
        expected_file="52005 - Operational Matters_ Uniform Dress Code (2583_19).docx",
        description="Where-is query by exact policy number",
        expected_metadata={
            "parent_title": "52005 - Operational Matters_ Uniform Dress Code (2583_19).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=23,
        query="Where can I find the IT Acceptable Use Policy document?",
        expected_policy="81100",
        expected_file="81100 - Information Technology Acceptable Use Policy (23666_5).docx",
        description="Where-can-I-find query for IT policy",
        expected_metadata={
            "parent_title": "81100 - Information Technology Acceptable Use Policy (23666_5).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=24,
        query="Where is the Code of Ethics document stored in the system?",
        expected_policy="31000",
        expected_file="31000 - Code of Ethics and Related Matters (9081_12).docx",
        description="Where-is with system storage context",
        expected_metadata={
            "parent_title": "31000 - Code of Ethics and Related Matters (9081_12).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=25,
        query="Give me the link to the Holiday Pay policy document",
        expected_policy="50715",
        expected_file="50715 - Hours Worked and Pay Administration_ Holiday Pay (23641_4).docx",
        description="Give-me-the-link for Holiday Pay",
        expected_metadata={
            "parent_title": "50715 - Hours Worked and Pay Administration_ Holiday Pay (23641_4).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=26,
        query="Give me the file path for Policy 87100 on AI and LLM",
        expected_policy="87100",
        expected_file="87100 - Generative Artificial Intelligence (AI) & Large Language Models (LLM) Policy (23653_5).docx",
        description="Give-me-the-path by policy number",
        expected_metadata={
            "parent_title": "87100 - Generative Artificial Intelligence (AI) & Large Language Models (LLM) Policy (23653_5).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=27,
        query="Provide the URL to the Mobile Device policy document",
        expected_policy="88100",
        expected_file="88100 - Mobile Device and Use Policy (23693_3).docx",
        description="Provide-URL for Mobile Device policy",
        expected_metadata={
            "parent_title": "88100 - Mobile Device and Use Policy (23693_3).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=28,
        query="Give me the blob storage path for the Short-Term Disability policy",
        expected_policy="51370",
        expected_file="51370 - Short-Term Disability (23317_1).docx",
        description="Blob-storage-path request for STD policy",
        expected_metadata={
            "parent_title": "51370 - Short-Term Disability (23317_1).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=29,
        query="What is the metadata_storage_path for the Blood Borne Pathogens Introduction document?",
        expected_policy="101100",
        expected_file="101100 - Blood Borne Pathogens Introduction (83_0).doc",
        description="Explicit metadata_storage_path request",
        expected_metadata={
            "parent_title": "101100 - Blood Borne Pathogens Introduction (83_0).doc",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=30,
        query="What is the full storage path for Policy 50455 on Probationary Period?",
        expected_policy="50455",
        expected_file="50455 - Hiring_ Probationary Period (23389_3).docx",
        description="Full-storage-path by policy number",
        expected_metadata={
            "parent_title": "50455 - Hiring_ Probationary Period (23389_3).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=31,
        query="Show me the file location and blob URL for the Computer Replacement Policy",
        expected_policy="84100",
        expected_file="84100 - Computer Replacement Policy (3261_3).doc",
        description="File-location-and-blob-URL combined request",
        expected_metadata={
            "parent_title": "84100 - Computer Replacement Policy (3261_3).doc",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=32,
        query="What does the Non-Uniform Dress Code policy say, and where is the document stored?",
        expected_policy="52010",
        expected_file="52010 - Operational Matters_ Non-Uniform Dress Code (23685_1).docx",
        description="Content + location combined query",
        expected_metadata={
            "parent_title": "52010 - Operational Matters_ Non-Uniform Dress Code (23685_1).docx",
            "container": "ask-hr-knowledge",
        },
    ),
    QueryExpectation(
        query_id=33,
        query="Summarize the Emergency Notification System policy and provide the document path",
        expected_policy="83400",
        expected_file="83400 - Emergency Notification System Policy (23234_0).doc",
        description="Summary + path combined query",
        expected_metadata={
            "parent_title": "83400 - Emergency Notification System Policy (23234_0).doc",
            "container": "ask-hr-knowledge",
        },
    ),
]

# Flat list of query strings for simple iteration
TEST_QUERIES: list[str] = [qe.query for qe in QUERY_EXPECTATIONS]
