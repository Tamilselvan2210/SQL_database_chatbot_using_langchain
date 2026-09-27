import sqlite3
from pathlib import Path
from typing import Optional, List

from pydantic import BaseModel, Field
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate


# ============================================================
# Pydantic models
# ============================================================

class ColumnDescription(BaseModel):
    column_name: str

    sql_type: str

    data_type: str

    semantic_description: str

    format: Optional[str] = None

    categories: Optional[List[str]] = None

    example_values: Optional[List[str]] = None

    null_count: int

    distinct_count: int


class TableDescription(BaseModel):
    table_name: str

    columns: List[ColumnDescription]





# ============================================================
# Get SQLite schema
# ============================================================

def get_table_schema(conn, table_name):

    query = f'PRAGMA table_info("{table_name}")'

    return conn.execute(query).fetchall()


# ============================================================
# Get all values from a column
# ============================================================

def get_column_values(conn, table_name, column_name):

    query = f'''
        SELECT "{column_name}"
        FROM "{table_name}"
        WHERE "{column_name}" IS NOT NULL
    '''

    rows = conn.execute(query).fetchall()

    return [row[0] for row in rows]


# ============================================================
# Check whether values are dates
# ============================================================

from datetime import datetime


def detect_date_format(values):

    if not values:
        return None

    formats = [
        ("%d-%m-%Y", "DD-MM-YYYY"),
        ("%d/%m/%Y", "DD/MM/YYYY"),
        ("%Y-%m-%d", "YYYY-MM-DD"),
        ("%Y/%m/%d", "YYYY/MM/DD"),
    ]

    # Check only strings
    if not all(isinstance(v, str) for v in values):
        return None

    # Limit testing to sample values
    sample = values[:100]

    for python_format, display_format in formats:

        try:

            for value in sample:
                datetime.strptime(value, python_format)

            return display_format

        except ValueError:
            continue

    return None


# ============================================================
# Check whether values are time
# ============================================================

def detect_time_format(values):

    if not values:
        return None

    formats = [
        ("%H:%M:%S", "HH:MM:SS"),
        ("%H:%M", "HH:MM"),
    ]

    if not all(isinstance(v, str) for v in values):
        return None

    sample = values[:100]

    for python_format, display_format in formats:

        try:

            for value in sample:
                datetime.strptime(value, python_format)

            return display_format

        except ValueError:
            continue

    return None


# ============================================================
# Determine data type
# ============================================================

def determine_data_type(values):

    if not values:
        return "unknown"

    if all(isinstance(v, int) for v in values):
        return "integer"

    if all(isinstance(v, (int, float)) for v in values):
        return "numeric"

    date_format = detect_date_format(values)

    if date_format:
        return "date"

    time_format = detect_time_format(values)

    if time_format:
        return "time"

    # Categorical detection
    unique_values = set(str(v) for v in values)

    # You can change this threshold
    if len(unique_values) <= 50:
        return "categorical"

    return "text"


# ============================================================
# Analyze one column
# ============================================================

def analyze_column(
    conn,
    table_name,
    column_name,
    sql_type
):

    # Get values
    values = get_column_values(
        conn,
        table_name,
        column_name
    )

    # Get NULL count
    null_query = f'''
        SELECT COUNT(*)
        FROM "{table_name}"
        WHERE "{column_name}" IS NULL
    '''

    null_count = conn.execute(null_query).fetchone()[0]

    distinct_values = list(
        dict.fromkeys(
            str(v) for v in values
        )
    )

    data_type = determine_data_type(values)

    # Date format
    detected_format = detect_date_format(values)

    # Time format
    if not detected_format:
        detected_format = detect_time_format(values)

    # --------------------------------------------------------
    # Categories
    # --------------------------------------------------------

    categories = None

    if data_type == "categorical":

        # IMPORTANT:
        # Keep the exact value from the database.
        categories = distinct_values

    # --------------------------------------------------------
    # Example values
    # --------------------------------------------------------

    example_values = distinct_values[:10]

    return {
        "column_name": column_name,
        "sql_type": sql_type,
        "data_type": data_type,
        "format": detected_format,
        "categories": categories,
        "example_values": example_values,
        "null_count": null_count,
        "distinct_count": len(distinct_values),
    }


# ============================================================
# Ask LLM for semantic description
# ============================================================

def get_semantic_description(
    llm,
    table_name,
    column_name,
    data_type,
    sql_type,
    example_values
):

    prompt = ChatPromptTemplate.from_template("""
You are a database schema documentation expert.

Create a concise semantic description for the SQL column.

Table name:
{table_name}

Column name:
{column_name}

SQL data type:
{sql_type}

Detected data type:
{data_type}

Example values:
{example_values}

Rules:

1. Describe what the column represents.
2. Use the table name and column name to infer the meaning.
3. Do NOT invent information that cannot reasonably be inferred.
4. Do not modify or correct any example values.
5. Return only the semantic description.
""")

    chain = prompt | llm

    response = chain.invoke({
        "table_name": table_name,
        "column_name": column_name,
        "sql_type": sql_type,
        "data_type": data_type,
        "example_values": example_values
    })

    return response.content.strip()


# ============================================================
# Analyze complete table
# ============================================================

def describe_table(conn, table_name,model):

    schema = get_table_schema(
        conn,
        table_name
    )

    columns = []

    for column in schema:

        # PRAGMA table_info:
        #
        # column[0] = cid
        # column[1] = name
        # column[2] = type

        column_name = column[1]
        sql_type = column[2]

        print(f"Analyzing column: {column_name}")

        # Python analysis
        column_info = analyze_column(
            conn,
            table_name,
            column_name,
            sql_type
        )

        # LLM semantic analysis
        semantic_description = get_semantic_description(
            llm=model,
            table_name=table_name,
            column_name=column_name,
            data_type=column_info["data_type"],
            sql_type=sql_type,
            example_values=column_info["example_values"]
        )

        # Create structured result
        description = ColumnDescription(
            column_name=column_name,
            sql_type=sql_type,
            data_type=column_info["data_type"],
            semantic_description=semantic_description,
            format=column_info["format"],
            categories=column_info["categories"],
            example_values=column_info["example_values"],
            null_count=column_info["null_count"],
            distinct_count=column_info["distinct_count"]
        )

        columns.append(description)

    return TableDescription(
        table_name=table_name,
        columns=columns
    )

