from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain.agents import create_agent
import sqlite3 as sq
import json
from pathlib import Path 
from pydantic import BaseModel, Field
from typing import Optional
from get_table_description import get_tables_description
from csv_file_synchronization import synchronize_csv_folder



#uploading the csv from the targeted folder to the SQL database


def get_schema_for_llm(cursor, table_name) -> str:
    '''return the schema of the SQL Database table'''

    cursor.execute(
        f'PRAGMA table_info("{table_name}")'
    )

    columns = cursor.fetchall()

    schema_text = f"Table: {table_name}\nColumns:\n"

    for column in columns:

        column_name = column[1]
        data_type = column[2]

        schema_text += f"- {column_name}: {data_type}\n"

    return schema_text


class Query_Creation (BaseModel):
    query_valid : bool
    sql_query : str
    explanation : str
    confidence : float = Field( ge= 0.0, le= 1.0)

class Query_Validator(BaseModel):
    sql_is_valid: bool
    query_answers_question: bool
    error_type: Optional[str] = None
    reason: str
    corrected_sql: Optional[str] = None

class Question_Routing(BaseModel):
    requires_sql: bool
    reason: str

model = ChatOllama(model='gemma4:e4b', temperature= 0.9)



#Validate whether the question need SQL query

def question_requires_sql(question: str, tables_description : dict) -> bool:
    """
    Determine whether the user's question requires querying the SQL database.
    Returns False for greetings, casual conversation, and unrelated questions.
    """
    routing_prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """
You route user questions for a retail SQL chatbot.

Return requires_sql=true only when the question needs information from the database.

these are the description of tables in the database - {tables_description}

Return requires_sql=false for:
- Greetings such as Hi or Hello
- Thanks or goodbye messages
- Casual conversation
- Questions unrelated to the retail transaction database

Return JSON matching:
{{
    "requires_sql": true or false,
    "reason": "brief explanation"
}}
"""
        ),
        ("human", "User question: {question}")
    ])

    routing_chain = (
        routing_prompt
        | model.with_structured_output(Question_Routing)
    )

    result = routing_chain.invoke({"question": question,"tables_description" : tables_description})

    if result.requires_sql == False:
        print (result.reason)
    return result.requires_sql

#create a llm instace to generate a sql query for user question


def create_query(question = str, tables_description = dict) :
    '''for the user question create a SQL query using the table schema.'''


    message = ChatPromptTemplate.from_messages([
        ('system',
        '''You are an expert SQLite SQL query generator.

Your task is to generate a SQL query that answers the user's question using ONLY the information available in the provided SQL table descriptions.

## INPUT

You will receive:

1. User Question
2. Tables Description

The Tables Description contains information about the available SQL tables, their columns, data types, and relevant information about the data.

## RULES

1. Generate a SQL query that directly answers the user's question.

2. Use ONLY the tables and columns provided in the Tables Description.

3. Do NOT invent:

   * table names
   * column names
   * data values
   * relationships between tables
   * information that does not exist in the provided database description.

4. If the requested information cannot be obtained from the provided tables, do not create a query based on assumptions. Set `query_valid` to `false` and explain why.

5. Do NOT create SQL statements that modify the database or its tables.

6. Do NOT generate:

   * INSERT
   * UPDATE
   * DELETE
   * DROP
   * ALTER
   * CREATE
   * TRUNCATE
   * REPLACE
     or any other database-modifying operation.

7. Generate only read-only SQL queries such as:

   * SELECT
   * JOIN
   * WHERE
   * GROUP BY
   * ORDER BY
   * HAVING
   * aggregate functions
   * subqueries
   * other valid read-only SQLite operations.

8. Table names MUST always be enclosed in double quotes.

   Example:

   SELECT *
   FROM "Retail-Store-Transactions-large";

9. If a table name contains spaces, hyphens, or other special characters, ALWAYS use double quotes around the complete table name.

10. The SQL query must use valid SQLite syntax.

11. The query must answer the user's question. Do not generate a query that is syntactically valid but unrelated to the question.

12. The `confidence` value MUST be a decimal number between `0.0` and `1.0`.

Examples:

* `0.2` = low confidence
* `0.5` = moderate confidence
* `0.8` = high confidence
* `1.0` = very high confidence

NEVER use a 1 to 5 confidence scale.

13. Do not include information in the explanation that is not supported by the Tables Description.

## OUTPUT REQUIREMENT

Your response MUST follow the `Query_Creation` Pydantic model provided by the application.

Do NOT return raw SQL by itself.

Do NOT return Markdown.

Do NOT wrap the response in `json or `sql code blocks.

Do NOT add any text before or after the structured response.

The `sql_query` field must contain the generated SQL query.

If the question cannot be answered using the available tables, set `query_valid` to `false` and clearly explain the reason. Do not invent a query.

## INPUT DATA

Tables Description:
{tables_description}

Generate the `Query_Creation` response now.
'''),
        ('human','Generate SQL query for the following question : {question}')
    ])

    structured_output = model.with_structured_output(Query_Creation)
    chain = message | structured_output
    response =chain.invoke(
        {'tables_description': tables_description ,'question':question}
    )

    return response

def fetch_db(cursor, query):
    if not query or not isinstance(query, str):
        raise ValueError("No valid SQL query was generated to execute.")

    cursor.execute(query)
    result = cursor.fetchall()
    return result

def Query_validation(question: str, sql_query: str, tables_description = list) -> Query_Validator:
    '''Validate the generated SQL query for safety, syntax, schema correctness,
    and whether it actually answers the user question.'''

    clean_sql = (sql_query or '').strip()

    if not clean_sql:
        return Query_Validator(
            sql_is_valid=False,
            query_answers_question=False,
            error_type='empty_query',
            reason='No SQL query was generated.',
            corrected_sql=None,
        )

    disallowed_keywords = (
        'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE',
        'TRUNCATE', 'REPLACE', 'ATTACH', 'DETACH', 'GRANT',
        'REVOKE', 'PRAGMA', 'EXEC', 'MERGE'
    )

    if not clean_sql.upper().startswith('SELECT'):
        return Query_Validator(
            sql_is_valid=False,
            query_answers_question=False,
            error_type='not_select',
            reason='The generated SQL is not a SELECT statement and may not be safe for read-only analysis.',
            corrected_sql=None,
        )

    if any(keyword in clean_sql.upper() for keyword in disallowed_keywords):
        return Query_Validator(
            sql_is_valid=False,
            query_answers_question=False,
            error_type='unsafe_query',
            reason='The query attempts to modify or execute database-level operations, which is not allowed.',
            corrected_sql=None,
        )


    validation_message = ChatPromptTemplate.from_messages([
        ('system',
         '''You are a SQL quality validator.
         Check whether the generated SQL query is correct, relevant, and safe.
         Use the user question and the table schema to determine:
         - whether the query is valid SQL for the database,
         - whether it references the correct columns and tables,
         - whether it logically answers the user's question,
         - whether the query is unrelated, vague, or not specific enough.

         Return a JSON object matching this structure:
         {{
           "sql_is_valid": bool,
           "query_answers_question": bool,
           "error_type": "string or null",
           "reason": "string",
           "corrected_sql": "string or null"
         }}

         Important rules:
         - Only accept SELECT queries.
         - Reject queries that try to modify the database.
         - Reject queries that do not correspond to the user request.
         - If the query is semantically off, set query_answers_question to false and explain why.
         - If there is a better SQL version, provide it in corrected_sql.
         '''),
        ('human',
         '''User question: {question}

         Database schema ,Column descriptions,Example rows: {tables_description}

         Candidate SQL query:
         {sql_query}
         ''')
    ])

    validation_output = model.with_structured_output(Query_Validator)
    chain = validation_message | validation_output

    result = chain.invoke({
        'question': question,
        'tables_description': tables_description,
        'sql_query': clean_sql,
    })

    return result

def create_validated_query(
    question: str,
    tables_description : list,
    max_attempts: int = 5
) -> str | None:

    # Generate the initial SQL
    creation_result: Query_Creation = create_query(
        question=question,
        tables_description= tables_description
    )

    # Extract SQL from Query_Creation
    sql_query = (creation_result.sql_query or '').strip()

    if not sql_query:
        print("No SQL query was generated.")
        return None

    for attempt in range(1, max_attempts + 1):

        print(f"\nValidation attempt: {attempt}")
        print(f"SQL Query:\n{sql_query}")
        
        # Validate the SQL
        validation_result: Query_Validator = Query_validation(
            question=question,
            sql_query=sql_query,
            tables_description= tables_description
        )

        # Check whether all validation conditions pass
        if (
            validation_result.sql_is_valid
            and validation_result.query_answers_question
            and validation_result.error_type is None
        ):
            print("Query passed validation.")
            return sql_query

        # Query validation failed
        print(f"Validation failed: {validation_result.reason}")
        print(validation_result.error_type,validation_result.query_answers_question,
              validation_result.sql_is_valid,validation_result.reason)

        # Use corrected SQL if available; otherwise stop retrying after this failed attempt.
        if validation_result.corrected_sql:
            sql_query = validation_result.corrected_sql.strip()
            if not sql_query:
                print("Corrected SQL is empty; stopping retry loop.")
                break
            continue

    print("No corrected SQL available.")

    print("Maximum validation attempts reached.")
    return None

def query_to_text(question = str,response = str,result = list) -> str:


    prompt = """you are an assistant who explain the result of the query for the question in text form. 
    create a humanly response for the question using the result.
    this is the question from the user : {question}
    this is the query generated by the model : {response}
    this is the result fetched for the query : {result}
    """

    prompt = ChatPromptTemplate.from_template(prompt)
    chain =  prompt | model
    final_response = chain.invoke({'question': question, 'response': response,'result':str(result)})
    return final_response


database = sq.connect('database/retail_transaction.db')
cursor = database.cursor()

print("\nPlease wait while I am updating the database...")

CSV_FOLDER = Path("./stored CSVs")
DATABASE_PATH = Path("./database/retail_transaction.db")
synchronize_csv_folder(csv_folder=CSV_FOLDER,database_path=DATABASE_PATH,model=model)
print("\nDatabase updated successfully.")

try :
    while True :

        user_question = input ("""------------------------------------------------------\nEnter your Question :('q' to end the session)\n------------------------------------------------------\n""")

        if user_question == 'q':
            break

        tables_description = get_tables_description(connection= database)

        bool_val = question_requires_sql(question=user_question,tables_description= tables_description)
            
        if bool_val:
            
            final_query = create_validated_query(question= user_question, tables_description=tables_description)

            if not final_query:
                print("I could not generate a valid SQL query for that question.")
                continue

            requested_info = fetch_db(cursor, final_query)

            response_to_user = query_to_text(question= user_question, response= final_query, result= requested_info)

            print(response_to_user.content)
        else:
            print('I am SQL summarisation agent. Please provide your question about the database.')

finally:
    cursor.close()