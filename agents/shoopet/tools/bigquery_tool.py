from google.cloud import bigquery

class BigQueryTools:
    def __init__(self, project: str):
        self.client = bigquery.Client(project=project)

    def execute_sql(self, query: str) -> str:
        """
        Execute a SQL query on BigQuery.
        Supports all SQL operations: CREATE TABLE, INSERT, UPDATE, DELETE, SELECT.

        Args:
            query: The SQL query to execute

        Returns:
            String representation of query results or success message
        """
        try:
            query_job = self.client.query(query)
            results = query_job.result()

            # If query returns rows, format them
            if results.total_rows and results.total_rows > 0:
                rows = [dict(row) for row in results]
                return f"Query executed successfully. Returned {len(rows)} rows:\n{rows}"
            else:
                return f"Query executed successfully. {results.total_rows or 0} rows affected."
        except Exception as e:
            return f"Error executing query: {str(e)}"

    def list_datasets(self) -> str:
        """
        List all datasets in the project.

        Returns:
            String representation of available datasets
        """
        try:
            datasets = list(self.client.list_datasets())
            if datasets:
                dataset_ids = [d.dataset_id for d in datasets]
                return f"Available datasets: {', '.join(dataset_ids)}"
            else:
                return "No datasets found in project."
        except Exception as e:
            return f"Error listing datasets: {str(e)}"

    def list_tables(self, dataset_id: str) -> str:
        """
        List all tables in a dataset.

        Args:
            dataset_id: The dataset ID to list tables from

        Returns:
            String representation of available tables
        """
        try:
            tables = list(self.client.list_tables(dataset_id))
            if tables:
                table_ids = [t.table_id for t in tables]
                return f"Tables in {dataset_id}: {', '.join(table_ids)}"
            else:
                return f"No tables found in dataset {dataset_id}."
        except Exception as e:
            return f"Error listing tables: {str(e)}"

    def get_table_schema(self, dataset_id: str, table_id: str) -> str:
        """
        Get the schema of a table.

        Args:
            dataset_id: The dataset ID
            table_id: The table ID

        Returns:
            String representation of table schema
        """
        try:
            table_ref = self.client.dataset(dataset_id).table(table_id)
            table = self.client.get_table(table_ref)
            schema_str = "\n".join([f"  - {field.name}: {field.field_type} ({field.mode})" for field in table.schema])
            return f"Schema for {dataset_id}.{table_id}:\n{schema_str}\nTotal rows: {table.num_rows}"
        except Exception as e:
            return f"Error getting table schema: {str(e)}"
