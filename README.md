# SQL Database Chatbot

A command-line chatbot that answers natural-language questions about retail CSV data using SQLite and a locally hosted Ollama language model. At startup, the app synchronizes the CSV files into a SQLite database; it then generates and validates read-only SQL queries to answer questions.

## Requirements

- Python 3.10 or newer
- [Ollama](https://ollama.com/) installed and running
- The `gemma4:e4b` model available in Ollama

Pull the model before starting the chatbot:

```powershell
ollama pull gemma4:e4b
```

## Setup

From the project root, create and activate a virtual environment, then install the dependencies:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirement.txt
```

## Run

Make sure Ollama is running, then launch the chatbot from the project root:

```powershell
python main_file.py
```

The program synchronizes CSV data into `database/retail_transaction.db` before opening the interactive prompt. Enter a question about the available retail data; enter `q` to quit.

## Data

Place CSV files in `stored CSVs/`. The included datasets are:

- `Online-Store-Orders.csv`
- `Retail-Store-Transactions-large.csv`

The application creates a SQLite table for each CSV file. Keep the `stored CSVs/` and `database/` paths relative to the project root when running the script.

## Project Structure

```text
.
├── main_file.py                 # Interactive chatbot and SQL workflow
├── csv_file_synchronization.py  # Imports and synchronizes CSV data
├── get_table_description.py     # Reads table descriptions from SQLite
├── table_description.py         # Builds descriptions of table columns
├── requirement.txt              # Python dependencies
├── database/                    # SQLite database files
└── stored CSVs/                 # Source CSV datasets
```

## Troubleshooting

- If the app cannot connect to Ollama, start the Ollama application or service and retry.
- If Ollama reports that `gemma4:e4b` is unavailable, run `ollama pull gemma4:e4b`.
- If no tables are found, check that the CSV files are in `stored CSVs/` and that you launched the script from the project root.
