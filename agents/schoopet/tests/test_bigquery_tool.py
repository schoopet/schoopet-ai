"""Unit tests for BigQueryTools."""
import pytest
from unittest.mock import MagicMock, patch
from agents.schoopet.bigquery_tool import BigQueryTools

PROJECT_ID = "test-project"

@pytest.fixture
def bigquery_tool():
    """Create a BigQueryTools instance with mocked client."""
    with patch("google.cloud.bigquery.Client") as mock_client_cls:
        tool = BigQueryTools(project=PROJECT_ID)
        # Trigger lazy init
        _ = tool.client
        
        # Inject mock
        tool._client = mock_client_cls.return_value
        
        yield tool

class TestBigQueryTools:
    """Tests for BigQueryTools class."""

    def test_initialization(self, bigquery_tool):
        """Should initialize with correct project."""
        assert bigquery_tool.project == PROJECT_ID

    def test_execute_sql_rows(self, bigquery_tool):
        """Should execute query and return rows."""
        mock_job = MagicMock()
        
        # Create a mock that behaves like a RowIterator
        mock_result = MagicMock()
        mock_result.total_rows = 2
        mock_result.__iter__.return_value = [
            {"col1": "val1", "col2": 1},
            {"col1": "val2", "col2": 2}
        ]
        
        mock_job.result.return_value = mock_result
        
        bigquery_tool.client.query.return_value = mock_job
        
        result = bigquery_tool.execute_sql("SELECT * FROM table")
        
        assert "Returned 2 rows" in result
        assert "val1" in result

    def test_execute_sql_no_rows(self, bigquery_tool):
        """Should handle queries with no result rows (e.g. INSERT)."""
        mock_job = MagicMock()
        mock_result = MagicMock()
        # Simulate DML where total_rows might be None or 0 for result set, 
        # but we want to verify the else block.
        # If the code checks 'if results.total_rows and results.total_rows > 0',
        # we need total_rows to be falsy to hit the else block.
        mock_result.total_rows = 0 
        
        mock_job.result.return_value = mock_result
        
        bigquery_tool.client.query.return_value = mock_job
        
        result = bigquery_tool.execute_sql("INSERT INTO table ...")
        
        assert "0 rows affected" in result

    def test_execute_sql_error(self, bigquery_tool):
        """Should handle execution errors."""
        bigquery_tool.client.query.side_effect = Exception("Query Failed")
        
        result = bigquery_tool.execute_sql("SELECT *")
        
        assert "Error executing query: Query Failed" in result

    def test_list_datasets(self, bigquery_tool):
        """Should list datasets."""
        mock_ds1 = MagicMock()
        mock_ds1.dataset_id = "ds1"
        mock_ds2 = MagicMock()
        mock_ds2.dataset_id = "ds2"
        
        bigquery_tool.client.list_datasets.return_value = [mock_ds1, mock_ds2]
        
        result = bigquery_tool.list_datasets()
        
        assert "ds1, ds2" in result

    def test_list_tables(self, bigquery_tool):
        """Should list tables."""
        mock_t1 = MagicMock()
        mock_t1.table_id = "t1"
        
        bigquery_tool.client.list_tables.return_value = [mock_t1]
        
        result = bigquery_tool.list_tables("ds1")
        
        assert "t1" in result
        bigquery_tool.client.list_tables.assert_called_once_with("ds1")

    def test_get_table_schema(self, bigquery_tool):
        """Should return table schema."""
        mock_field = MagicMock()
        mock_field.name = "col1"
        mock_field.field_type = "STRING"
        mock_field.mode = "NULLABLE"
        
        mock_table = MagicMock()
        mock_table.schema = [mock_field]
        mock_table.num_rows = 100
        
        bigquery_tool.client.get_table.return_value = mock_table
        
        result = bigquery_tool.get_table_schema("ds1", "t1")
        
        assert "col1: STRING (NULLABLE)" in result
        assert "Total rows: 100" in result
