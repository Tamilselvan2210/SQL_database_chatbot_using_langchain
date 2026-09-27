import sqlite3
import json


def get_tables_description(
    connection: sqlite3.Connection,) -> dict:

    cursor = connection.cursor()

    query_to_get_table = '''SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        ORDER BY name'''

    cursor.execute(query_to_get_table)

    tables_list = cursor.fetchall()

    table_names = [row[0] for row in tables_list]

    table_names = [
        table for table in table_names
        if table not in ['csv_sync_log', 'table_description']
    ]
  

    
    query = """
        SELECT table_description
        FROM table_description
        WHERE table_name = ?
    """
    table_description = {}
    int = 1
    for table in table_names :
        cursor.execute(query, (table,))

        row = cursor.fetchone()

        if row is None:
            print(f"No record found for: {table}")
            return

        # SQL TEXT → Python dictionary
        json_data = json.loads(row[0])
        table_id = 'table'+str(int)

        table_description[table_id]= json_data
        int += 1
        #print(json_data)



    return table_description

