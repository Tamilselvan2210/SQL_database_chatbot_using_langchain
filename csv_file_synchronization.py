
from pathlib import Path
from datetime import datetime
import sqlite3
import hashlib
import pandas as pd
from langchain_ollama import OllamaLLM
from table_description import describe_table
from pydantic import BaseModel

# ============================================================
# CONFIGURATION
# ============================================================

CSV_FOLDER = Path("./stored CSVs")
DATABASE_PATH = Path("./database/retail_transaction.db")


# ============================================================
# 1. CONVERT PANDAS DATATYPE TO SQLITE DATATYPE
# ============================================================

def pandas_to_sqlite_type(dtype):

    if pd.api.types.is_integer_dtype(dtype):
        return "INTEGER"

    elif pd.api.types.is_float_dtype(dtype):
        return "REAL"

    elif pd.api.types.is_bool_dtype(dtype):
        return "INTEGER"

    elif pd.api.types.is_datetime64_any_dtype(dtype):
        return "TEXT"

    else:
        return "TEXT"


# ============================================================
# 2. CREATE CREATE-TABLE QUERY
# ============================================================

def create_table_query(df, table_name):

    columns = []

    for column in df.columns:

        sqlite_type = pandas_to_sqlite_type(
            df[column].dtype
        )

        columns.append(
            f'"{column}" {sqlite_type}'
        )

    query = f"""
    CREATE TABLE IF NOT EXISTS "{table_name}" (
        {", ".join(columns)}
    )
    """

    return query


# ============================================================
# 3. CREATE INSERT QUERY
# ============================================================

def create_insert_query(df, table_name):

    column_names = ", ".join(
        f'"{column}"'
        for column in df.columns
    )

    placeholders = ", ".join(
        ["?"] * len(df.columns)
    )

    query = f"""
    INSERT INTO "{table_name}"
    ({column_names})
    VALUES ({placeholders})
    """

    return query


# ============================================================
# 4. CONVERT DATAFRAME ROW TO SQLITE VALUES
# ============================================================

def get_row_values(row):

    values = []

    for value in row:

        # NaN / NaT -> None
        if pd.isna(value):

            values.append(None)

        # Pandas Timestamp -> string
        elif isinstance(value, pd.Timestamp):

            values.append(
                value.isoformat()
            )

        # NumPy value -> Python value
        elif hasattr(value, "item"):

            values.append(
                value.item()
            )

        else:

            values.append(value)

    return tuple(values)


# ============================================================
# 5. CHECK WHETHER SQL TABLE EXISTS
# ============================================================

def table_exists(
    cursor,
    table_name
):

    query = """
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
    AND name = ?
    """

    cursor.execute(
        query,
        (table_name,)
    )

    return cursor.fetchone() is not None


# ============================================================
# 6. GET FILE HASH
# ============================================================

def get_file_hash(
    file_path: Path
) -> str:

    sha256 = hashlib.sha256()

    with open(
        file_path,
        "rb"
    ) as file:

        while chunk := file.read(
            1024 * 1024
        ):

            sha256.update(chunk)

    return sha256.hexdigest()


# ============================================================
# 7. GET CSV FILE INFORMATION
# ============================================================

def get_file_information(
    csv_file: Path
):

    stat = csv_file.stat()

    creation_time = datetime.fromtimestamp(
        stat.st_birthtime
    ).isoformat(
        timespec="seconds"
    )

    modification_time = datetime.fromtimestamp(
        stat.st_mtime
    ).isoformat(
        timespec="seconds"
    )

    file_size = stat.st_size

    file_hash = get_file_hash(
        csv_file
    )

    table_name = csv_file.stem

    return {

        "file_name": csv_file.name,

        "file_path": str(
            csv_file.resolve()
        ),

        "table_name": table_name,

        "creation_time": creation_time,

        "modification_time": modification_time,

        "file_size": file_size,

        "file_hash": file_hash
    }


# ============================================================
# 8. CREATE CSV SYNCHRONIZATION LOG TABLE
# ============================================================

def create_csv_sync_log_table(
    connection
):

    connection.execute("""
        CREATE TABLE IF NOT EXISTS csv_sync_log (

            file_name TEXT PRIMARY KEY,

            file_path TEXT NOT NULL,

            table_name TEXT NOT NULL,

            file_creation_time TEXT NOT NULL,

            file_modification_time TEXT NOT NULL,

            file_size INTEGER,

            file_hash TEXT,

            last_processed_time TEXT NOT NULL
        )
    """)

    connection.commit()


# ============================================================
# 9. GET PREVIOUS FILE INFORMATION
# ============================================================

def get_file_record(
    connection,
    file_name
):

    cursor = connection.execute("""

        SELECT

            file_name,
            file_path,
            table_name,
            file_creation_time,
            file_modification_time,
            file_size,
            file_hash,
            last_processed_time

        FROM csv_sync_log

        WHERE file_name = ?

    """, (file_name,))

    return cursor.fetchone()


# ============================================================
# 10. CHECK WHETHER FILE HAS CHANGED
# ============================================================

def file_has_changed(
    file_info,
    database_record
):

    # --------------------------------------------------------
    # No previous record = new file
    # --------------------------------------------------------

    if database_record is None:

        return True


    # --------------------------------------------------------
    # Previous values
    # --------------------------------------------------------

    old_creation_time = database_record[3]

    old_modification_time = database_record[4]

    old_file_size = database_record[5]

    old_file_hash = database_record[6]


    # --------------------------------------------------------
    # Compare creation time
    # --------------------------------------------------------

    if (
        file_info["creation_time"]
        != old_creation_time
    ):

        return True


    # --------------------------------------------------------
    # Compare modification time
    # --------------------------------------------------------

    if (
        file_info["modification_time"]
        != old_modification_time
    ):

        return True


    # --------------------------------------------------------
    # Compare file size
    # --------------------------------------------------------

    if (
        file_info["file_size"]
        != old_file_size
    ):

        return True


    # --------------------------------------------------------
    # Compare hash
    # --------------------------------------------------------

    if (
        file_info["file_hash"]
        != old_file_hash
    ):

        return True


    return False


# ============================================================
# 11. UPDATE SYNCHRONIZATION LOG
# ============================================================

def update_sync_log(
    connection,
    file_info
):

    processed_time = datetime.now().isoformat(
        timespec="seconds"
    )

    connection.execute("""

        INSERT INTO csv_sync_log (

            file_name,
            file_path,
            table_name,
            file_creation_time,
            file_modification_time,
            file_size,
            file_hash,
            last_processed_time

        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(file_name)

        DO UPDATE SET

            file_path =
                excluded.file_path,

            table_name =
                excluded.table_name,

            file_creation_time =
                excluded.file_creation_time,

            file_modification_time =
                excluded.file_modification_time,

            file_size =
                excluded.file_size,

            file_hash =
                excluded.file_hash,

            last_processed_time =
                excluded.last_processed_time

    """, (

        file_info["file_name"],

        file_info["file_path"],

        file_info["table_name"],

        file_info["creation_time"],

        file_info["modification_time"],

        file_info["file_size"],

        file_info["file_hash"],

        processed_time
    ))

    connection.commit()


# ============================================================
# 12. CREATE / UPDATE SQL TABLE FOR ONE CSV
# ============================================================

def create_sql_tables(
    csv_file,
    database_connection,
    replace_existing: bool = False
):

    csv_file = Path(csv_file)

    # --------------------------------------------------------
    # Check CSV
    # --------------------------------------------------------

    if not csv_file.exists():

        raise FileNotFoundError(
            f"CSV file not found: {csv_file}"
        )

    if csv_file.suffix.lower() != ".csv":

        raise ValueError(
            f"File is not a CSV file: {csv_file}"
        )


    # --------------------------------------------------------
    # Table name = CSV filename without extension
    # --------------------------------------------------------

    table_name = csv_file.stem

    cursor = database_connection.cursor()


    # --------------------------------------------------------
    # Check existing table
    # --------------------------------------------------------

    if table_exists(
        cursor,
        table_name
    ):

        # ----------------------------------------------------
        # Existing table + replacement not requested
        # ----------------------------------------------------

        if not replace_existing:

            print(
                f"Table '{table_name}' already exists."
            )

            print(
                "Skipping."
            )

            return {

                "status": "skipped",

                "file": csv_file.name,

                "table": table_name
            }


        # ----------------------------------------------------
        # Existing table + replacement requested
        # ----------------------------------------------------

        print(
            f"Replacing table '{table_name}'..."
        )

        cursor.execute(
            f'DROP TABLE IF EXISTS "{table_name}"'
        )


    # --------------------------------------------------------
    # Read CSV
    # --------------------------------------------------------

    print(
        f"Reading CSV: {csv_file.name}"
    )

    df = pd.read_csv(
        csv_file
    )


    print(
        f"Rows: {len(df)}"
    )

    print(
        f"Columns: {list(df.columns)}"
    )


    try:

        # ----------------------------------------------------
        # Create SQL table
        # ----------------------------------------------------

        table_query = create_table_query(
            df,
            table_name
        )

        cursor.execute(
            table_query
        )


        # ----------------------------------------------------
        # Create INSERT query
        # ----------------------------------------------------

        insert_query = create_insert_query(
            df,
            table_name
        )


        # ----------------------------------------------------
        # Insert rows
        # ----------------------------------------------------

        print(
            "Inserting rows..."
        )

        for _, row in df.iterrows():

            values = get_row_values(
                row
            )

            cursor.execute(
                insert_query,
                values
            )


        # ----------------------------------------------------
        # Commit
        # ----------------------------------------------------

        database_connection.commit()


        # ----------------------------------------------------
        # Determine status
        # ----------------------------------------------------

        if replace_existing:

            status = "updated"

        else:

            status = "created"


        print(
            f"Table '{table_name}' "
            f"{status} successfully."
        )


        return {

            "status": status,

            "file": csv_file.name,

            "table": table_name,

            "rows": len(df),

            "columns": list(df.columns)
        }


    except Exception:

        database_connection.rollback()

        raise



def save_table_description(
    connection: sqlite3.Connection,
    table_name: str,
    table_description: BaseModel
) -> None:

    # ========================================================
    # 1. Create table_description table if it doesn't exist
    # ========================================================

    connection.execute("""
        CREATE TABLE IF NOT EXISTS table_description (

            table_name TEXT PRIMARY KEY,

            table_description TEXT NOT NULL
        )
    """)


    # ========================================================
    # 2. Convert Pydantic object to JSON string
    # ========================================================

    description_json = (
        table_description.model_dump_json(
            indent=2
        )
    )


    # ========================================================
    # 3. Insert or update the description
    # ========================================================

    connection.execute("""
        INSERT INTO table_description (
            table_name,
            table_description
        )

        VALUES (?, ?)

        ON CONFLICT(table_name)

        DO UPDATE SET

            table_description =
                excluded.table_description

    """, (
        table_name,
        description_json
    ))


    # ========================================================
    # 4. Save changes
    # ========================================================

    connection.commit()

    print('Table description saved sucessfully')


# ============================================================
# 14. SYNCHRONIZE ONE CSV FILE
# ============================================================

def synchronize_csv_file(
    csv_file,
    connection,model
):

    csv_file = Path(csv_file)


    # --------------------------------------------------------
    # Get current file information
    # --------------------------------------------------------

    file_info = get_file_information(
        csv_file
    )


    print("\n" + "=" * 60)

    print(
        f"Checking: {file_info['file_name']}"
    )


    # --------------------------------------------------------
    # Get previous synchronization record
    # --------------------------------------------------------

    database_record = get_file_record(
        connection,
        file_info["file_name"]
    )


    # ========================================================
    # NEW FILE
    # ========================================================

    if database_record is None:

        print(
            "New CSV file detected."
        )


        replace_existing: bool = False


        result = create_sql_tables(

            csv_file=csv_file,

            database_connection=connection,

            replace_existing=replace_existing
        )


        # ----------------------------------------------------
        # Create table description
        # ----------------------------------------------------

        if result["status"] == "created":

            table_description = describe_table(
                table_name=result["table"],
                conn=connection,
                model=model
            )

            save_table_description(
                    connection=connection,
                    table_name=result["table"],
                    table_description=table_description
                )


        # ----------------------------------------------------
        # Update synchronization log
        # ----------------------------------------------------

        update_sync_log(
            connection,
            file_info
        )


        return result


    # ========================================================
    # EXISTING FILE
    # ========================================================

    changed = file_has_changed(
        file_info,
        database_record
    )


    # ========================================================
    # FILE NOT CHANGED
    # ========================================================

    if not changed:

        print(
            "No change detected."
        )

        return {

            "status": "unchanged",

            "file": csv_file.name,

            "table": file_info["table_name"]
        }


    # ========================================================
    # FILE MODIFIED
    # ========================================================

    print(
        "CSV file has changed."
    )


    replace_existing: bool = True


    result = create_sql_tables(

        csv_file=csv_file,

        database_connection=connection,

        replace_existing=replace_existing
    )


    # --------------------------------------------------------
    # Update table description
    # --------------------------------------------------------

    if result["status"] == "updated":

        table_description = describe_table(
            table_name=result["table"],
            conn=connection,
            model=model
        )


    # --------------------------------------------------------
    # Update synchronization log
    # --------------------------------------------------------

    update_sync_log(
        connection,
        file_info
    )

    save_table_description(
        connection=connection,
        table_name=result["table"],
        table_description=table_description
    )

    return result


# ============================================================
# 15. SYNCHRONIZE CSV FOLDER
# ============================================================

def synchronize_csv_folder(
    csv_folder,
    database_path,
    model
):

    csv_folder = Path(
        csv_folder
    ).resolve()

    database_path = Path(
        database_path
    ).resolve()


    # --------------------------------------------------------
    # Check CSV folder
    # --------------------------------------------------------

    if not csv_folder.exists():

        raise FileNotFoundError(
            f"CSV folder not found: {csv_folder}"
        )


    # --------------------------------------------------------
    # Create database parent folder
    # --------------------------------------------------------

    database_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )


    # --------------------------------------------------------
    # Open database
    # --------------------------------------------------------

    connection = sqlite3.connect(
        database_path
    )


    try:

        # ----------------------------------------------------
        # Create synchronization log
        # ----------------------------------------------------

        create_csv_sync_log_table(
            connection
        )


        # ----------------------------------------------------
        # Find CSV files
        # ----------------------------------------------------

        csv_files = list(
            csv_folder.glob("*.csv")
        )


        print(
            f"Found {len(csv_files)} CSV file(s)."
        )


        # ----------------------------------------------------
        # Process each CSV
        # ----------------------------------------------------

        results = []


        for csv_file in csv_files:

            result = synchronize_csv_file(
                csv_file,
                connection,
                model=model
            )

            results.append(
                result
            )


        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        print("\n" + "=" * 60)

        print(
            "CSV SYNCHRONIZATION COMPLETED"
        )

        print("=" * 60)


        for result in results:

            print(
                f"{result['file']} -> "
                f"{result['table']} -> "
                f"{result['status']}"
            )


        return results


    finally:

        connection.close()

