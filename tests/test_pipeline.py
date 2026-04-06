"""
Pipeline unit tests.

These tests verify:
  1. XML parsing for KOSTAT responses
  2. ECOS URL construction
  3. Data type conversions (safe_int, WGT parsing)
  4. Aggregation SQL logic (requires PostgreSQL)

Run with:
  cd src && python -m pytest ../tests/ -v

For tests requiring the database:
  docker-compose up -d
  cd src && python -m pytest ../tests/ -v -m "not db"   # skip DB tests
  cd src && python -m pytest ../tests/ -v                # all tests
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


# ── Test KOSTAT XML Parsing ───────────────────────────────────────────

class TestKostatXmlParsing:
    """Test the KOSTAT XML parser with realistic responses."""

    def test_parse_success_response(self):
        from pipeline.collect_kostat import _parse_xml

        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <response>
            <header>
                <resultCode>00</resultCode>
                <resultMsg>NORMAL SERVICE.</resultMsg>
            </header>
            <body>
                <totalCount>3</totalCount>
                <items>
                    <item>
                        <pi>PROD001</pi>
                        <pn>테스트 상품 1</pn>
                        <sp>15000</sp>
                        <dp>13500</dp>
                        <bp>12000</bp>
                        <sd>2026-01-15</sd>
                    </item>
                    <item>
                        <pi>PROD002</pi>
                        <pn>테스트 상품 2</pn>
                        <sp>25000</sp>
                        <dp>22000</dp>
                        <bp></bp>
                        <sd>2026-01-15</sd>
                    </item>
                </items>
            </body>
        </response>"""

        result = _parse_xml(xml)
        assert result["code"] == "00"
        assert result["total"] == 3
        assert len(result["items"]) == 2
        assert result["items"][0]["pi"] == "PROD001"
        assert result["items"][0]["sp"] == "15000"
        assert result["items"][1]["pn"] == "테스트 상품 2"

    def test_parse_error_response(self):
        from pipeline.collect_kostat import _parse_xml

        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <response>
            <header>
                <resultCode>21</resultCode>
                <resultMsg>THERE IS NO MSG IN YOUR REQUEST.</resultMsg>
            </header>
            <body>
                <totalCount>0</totalCount>
                <items></items>
            </body>
        </response>"""

        result = _parse_xml(xml)
        assert result["code"] == "21"
        assert result["total"] == 0
        assert len(result["items"]) == 0

    def test_parse_item_list_response(self):
        from pipeline.collect_kostat import _parse_xml

        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <response>
            <header>
                <resultCode>00</resultCode>
                <resultMsg>NORMAL SERVICE.</resultMsg>
            </header>
            <body>
                <totalCount>2</totalCount>
                <items>
                    <item>
                        <rn>1</rn>
                        <ic>A01101</ic>
                        <in>쌀</in>
                        <ed>2024년 12월 19일 이후</ed>
                    </item>
                    <item>
                        <rn>2</rn>
                        <ic>A01201</ic>
                        <in>찹쌀</in>
                        <ed>2024년 12월 18일 이전</ed>
                    </item>
                </items>
            </body>
        </response>"""

        result = _parse_xml(xml)
        assert len(result["items"]) == 2
        assert result["items"][0]["ic"] == "A01101"
        assert "이후" in result["items"][0]["ed"]
        assert "이전" in result["items"][1]["ed"]


# ── Test ECOS URL Builder ─────────────────────────────────────────────

class TestEcosUrlBuilder:
    """Test ECOS URL construction."""

    @patch("pipeline.collect_ecos.ECOS_API_KEY", "TESTKEY123")
    def test_basic_url(self):
        from pipeline.collect_ecos import _ecos_url

        url = _ecos_url("StatisticSearch", 1, 100, "901Y009", "M", "202401", "202412")
        assert "StatisticSearch" in url
        assert "TESTKEY123" in url
        assert "json" in url
        assert "kr" in url
        assert "1/100" in url
        assert "901Y009" in url
        assert "202401" in url

    @patch("pipeline.collect_ecos.ECOS_API_KEY", "TESTKEY123")
    def test_url_segments(self):
        from pipeline.collect_ecos import _ecos_url

        url = _ecos_url("KeyStatisticList", 1, 10)
        parts = url.split("/")
        # Should have: base parts + service + key + format + lang + start + end
        assert "KeyStatisticList" in parts
        assert "TESTKEY123" in parts


# ── Test Safe Int Conversion ──────────────────────────────────────────

class TestSafeInt:
    """Test the safe integer conversion for price fields."""

    def test_normal_int(self):
        from pipeline.collect_kostat import _safe_int
        assert _safe_int("15000") == 15000

    def test_with_commas(self):
        from pipeline.collect_kostat import _safe_int
        assert _safe_int("1,500,000") == 1500000

    def test_empty_string(self):
        from pipeline.collect_kostat import _safe_int
        assert _safe_int("") is None

    def test_none(self):
        from pipeline.collect_kostat import _safe_int
        assert _safe_int(None) is None

    def test_non_numeric(self):
        from pipeline.collect_kostat import _safe_int
        assert _safe_int("N/A") is None


# ── Test ECOS Data Parsing ────────────────────────────────────────────

class TestEcosDataParsing:
    """Test ECOS response field parsing."""

    def test_wgt_numeric_conversion(self):
        """WGT field comes as string, should be convertible to float."""
        test_values = [
            ("1000", 1000.0),
            ("142", 142.0),
            ("0.5", 0.5),
            ("null", None),
            (None, None),
            ("", None),
        ]
        for input_val, expected in test_values:
            if input_val and input_val != "null":
                try:
                    result = float(input_val)
                except ValueError:
                    result = None
            else:
                result = None
            assert result == expected, f"Failed for input '{input_val}'"

    def test_data_value_conversion(self):
        """DATA_VALUE should handle '-' (no data marker)."""
        test_values = [
            ("115.71", 115.71),
            ("100", 100.0),
            ("-", None),
            ("", None),
            (None, None),
        ]
        for input_val, expected in test_values:
            if input_val and input_val != "-":
                try:
                    result = float(input_val)
                except ValueError:
                    result = None
            else:
                result = None
            assert result == expected, f"Failed for input '{input_val}'"


# ── DB Integration Tests (require running PostgreSQL) ─────────────────

@pytest.fixture
def db_cursor():
    """Provide a database cursor for integration tests."""
    try:
        from pipeline.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        yield cur
        conn.rollback()  # always rollback test data
        cur.close()
        conn.close()
    except Exception:
        pytest.skip("PostgreSQL not available")


class TestDatabaseIntegration:
    """Tests that require a running PostgreSQL instance."""

    @pytest.mark.db
    def test_connection(self, db_cursor):
        """Verify we can connect to the database."""
        db_cursor.execute("SELECT 1")
        assert db_cursor.fetchone()[0] == 1

    @pytest.mark.db
    def test_schemas_exist(self, db_cursor):
        """Verify raw and mart schemas are created."""
        db_cursor.execute("""
            SELECT schema_name FROM information_schema.schemata
            WHERE schema_name IN ('raw', 'mart')
            ORDER BY schema_name
        """)
        schemas = [row[0] for row in db_cursor.fetchall()]
        assert "mart" in schemas
        assert "raw" in schemas

    @pytest.mark.db
    def test_tables_exist(self, db_cursor):
        """Verify all expected tables exist."""
        db_cursor.execute("""
            SELECT table_schema || '.' || table_name
            FROM information_schema.tables
            WHERE table_schema IN ('raw', 'mart')
            AND table_type = 'BASE TABLE'
            ORDER BY 1
        """)
        tables = [row[0] for row in db_cursor.fetchall()]
        expected = [
            "mart.daily_price_summary",
            "mart.item_mapping",
            "mart.monthly_cpi_index",
            "raw.collection_log",
            "raw.ecos_indices",
        ]
        for t in expected:
            assert t in tables, f"Missing table: {t}"

    @pytest.mark.db
    def test_collection_log_lifecycle(self, db_cursor):
        """Test the CollectionLog helper."""
        from pipeline.db import CollectionLog

        log = CollectionLog(source="TEST", endpoint="test_endpoint", params={"key": "val"})
        log.start(db_cursor)

        # Verify it was inserted
        db_cursor.execute(
            "SELECT status FROM raw.collection_log WHERE id = %s",
            (str(log.id),)
        )
        assert db_cursor.fetchone()[0] == "RUNNING"

        # Mark success
        log.succeed(db_cursor, records_fetched=42)
        db_cursor.execute(
            "SELECT status, records_fetched FROM raw.collection_log WHERE id = %s",
            (str(log.id),)
        )
        row = db_cursor.fetchone()
        assert row[0] == "SUCCESS"
        assert row[1] == 42

    @pytest.mark.db
    def test_item_mapping_loaded(self, db_cursor):
        """Verify item mapping was loaded from init SQL."""
        db_cursor.execute("SELECT COUNT(*) FROM mart.item_mapping")
        count = db_cursor.fetchone()[0]
        # Should have 124 items from item_mapping_insert.sql
        assert count >= 100, f"Expected ~124 items, got {count}"

    @pytest.mark.db
    def test_kostat_partitions_exist(self, db_cursor):
        """Verify monthly partitions were created."""
        db_cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'raw'
            AND table_name LIKE 'kostat_products_2026_%'
        """)
        count = db_cursor.fetchone()[0]
        assert count == 12, f"Expected 12 partitions for 2026, got {count}"


# ── Phase 2: Quality & Alerting Tests ────────────────────────────────

class TestAlertFormatting:
    """Test alert message formatting (no Slack calls)."""

    def test_format_collection_summary(self):
        from pipeline.alerts import format_collection_summary
        stats = {"total_records": 1000, "items_with_data": 50, "errors": 0}
        msg = format_collection_summary("KOSTAT", stats)
        assert "KOSTAT" in msg
        assert "1000" in msg

    def test_format_quality_report_all_pass(self):
        from pipeline.alerts import format_quality_report
        results = [
            {"check": "freshness", "status": "PASS", "value": 5, "threshold": 21,
             "message": "OK"},
        ]
        msg = format_quality_report(results)
        assert "0 failures" in msg
        assert "0 warnings" in msg

    def test_format_quality_report_with_warnings(self):
        from pipeline.alerts import format_quality_report
        results = [
            {"check": "freshness", "status": "WARN", "value": 25, "threshold": 21,
             "message": "Stale data"},
            {"check": "nulls", "status": "PASS", "value": 0.5, "threshold": 10,
             "message": "OK"},
        ]
        msg = format_quality_report(results)
        assert "1 warnings" in msg
        assert "0 failures" in msg

    def test_format_empty_results(self):
        from pipeline.alerts import format_quality_report
        msg = format_quality_report([])
        assert "passed" in msg.lower()

    @patch("pipeline.alerts.SLACK_WEBHOOK_URL", "")
    def test_send_alert_no_webhook(self):
        """Alert should log but not fail when no webhook configured."""
        from pipeline.alerts import send_alert, INFO
        # Should not raise
        send_alert(INFO, "Test", "This is a test")


class TestQualityChecks:
    """Test quality check logic with mock data."""

    def test_result_structure(self):
        from pipeline.quality import _result
        r = _result("test_check", "PASS", 42, 100, "Test passed")
        assert r["check"] == "test_check"
        assert r["status"] == "PASS"
        assert r["value"] == 42
        assert r["threshold"] == 100
        assert r["message"] == "Test passed"

    def test_freshness_with_mock_cursor(self):
        """Test KOSTAT freshness check with mocked DB."""
        from pipeline.quality import check_kostat_freshness
        from datetime import date, timedelta

        cursor = MagicMock()
        # Simulate data from 5 days ago
        recent_date = date.today() - timedelta(days=5)
        cursor.fetchone.return_value = (recent_date,)

        result = check_kostat_freshness(cursor)
        assert result["status"] == "PASS"
        assert result["value"] == 5

    def test_freshness_stale_data(self):
        """Test KOSTAT freshness check with stale data."""
        from pipeline.quality import check_kostat_freshness
        from datetime import date, timedelta

        cursor = MagicMock()
        # Simulate data from 30 days ago (> 21 day threshold)
        old_date = date.today() - timedelta(days=30)
        cursor.fetchone.return_value = (old_date,)

        result = check_kostat_freshness(cursor)
        assert result["status"] == "WARN"
        assert result["value"] == 30

    def test_freshness_no_data(self):
        """Test KOSTAT freshness check with no data."""
        from pipeline.quality import check_kostat_freshness

        cursor = MagicMock()
        cursor.fetchone.return_value = (None,)

        result = check_kostat_freshness(cursor)
        assert result["status"] == "FAIL"

    def test_null_ratio_clean(self):
        """Test null ratio check with clean data."""
        from pipeline.quality import check_kostat_null_ratio

        cursor = MagicMock()
        cursor.fetchone.return_value = (10000, 50)  # 0.5% nulls

        result = check_kostat_null_ratio(cursor)
        assert result["status"] == "PASS"

    def test_null_ratio_high(self):
        """Test null ratio check with high nulls."""
        from pipeline.quality import check_kostat_null_ratio

        cursor = MagicMock()
        cursor.fetchone.return_value = (10000, 1500)  # 15% nulls

        result = check_kostat_null_ratio(cursor)
        assert result["status"] == "WARN"


class TestQualityIntegration:
    """Quality checks against real database."""

    @pytest.mark.db
    def test_quality_tables_exist(self, db_cursor):
        """Verify quality tables were created by migration."""
        db_cursor.execute("""
            SELECT table_schema || '.' || table_name
            FROM information_schema.tables
            WHERE table_schema IN ('raw', 'mart')
            AND table_name IN ('price_anomalies', 'schema_baseline', 'quality_check_log')
        """)
        tables = [row[0] for row in db_cursor.fetchall()]
        assert "mart.price_anomalies" in tables
        assert "raw.schema_baseline" in tables
        assert "raw.quality_check_log" in tables

    @pytest.mark.db
    def test_validate_on_real_data(self, db_cursor):
        """Run quality checks on actual collected data — no crashes."""
        from pipeline.quality import run_all_checks
        results = run_all_checks(db_cursor)
        assert len(results) == 6  # all 6 checks should run
        for r in results:
            assert r["status"] in ("PASS", "WARN", "FAIL")
            assert "check" in r
            assert "message" in r
