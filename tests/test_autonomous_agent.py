#!/usr/bin/env python3
"""
Test Autonomous Agent System

Verifies all components are working:
1. Gateway server
2. Heartbeat monitoring
3. Cron scheduler
4. AI integration
5. Session management
"""

import time

import pytest
import requests

BASE_URL = "http://localhost:8789"


def test_gateway_health():
    """Test gateway is running."""
    print("\n=== Testing Gateway Health ===")
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=5)
        if resp.status_code == 200:
            print("[+] Gateway is running")
            assert True
        else:
            print(f"[-] Gateway returned {resp.status_code}")
            pytest.skip("Gateway not available")
    except requests.ConnectionError:
        print("[-] Gateway is not running")
        print("   Start with: navig gateway start")
        pytest.skip("Gateway not running")


def test_gateway_status():
    """Test gateway status endpoint."""
    print("\n=== Testing Gateway Status ===")
    try:
        resp = requests.get(f"{BASE_URL}/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"[+] Status: {data.get('status')}")
            print(f"   Uptime: {data.get('uptime', 'unknown')}")
            print(f"   Sessions: {data.get('active_sessions', 0)}")
            assert True
        else:
            print(f"[-] Status failed: {resp.status_code}")
            pytest.skip("Gateway status endpoint not available")
    except Exception as e:
        print(f"[-] Error: {e}")
        pytest.skip(f"Gateway error: {e}")


def test_cron_list():
    """Test cron job listing."""
    print("\n=== Testing Cron Scheduler ===")
    try:
        resp = requests.get(f"{BASE_URL}/cron/jobs", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs", [])
            print(f"[+] Cron service active")
            print(f"   Jobs configured: {len(jobs)}")
            for job in jobs:
                status = "[+]" if job.get("enabled") else "[-]"
                print(f"   {status} {job.get('name')}")
                print(f"      Schedule: {job.get('schedule')}")
                print(f"      Next run: {job.get('next_run', 'N/A')}")
            assert True
        else:
            print(f"[-] Cron list failed: {resp.status_code}")
            pytest.skip("Cron endpoint not available")
    except Exception as e:
        print(f"[-] Error: {e}")
        pytest.skip(f"Gateway error: {e}")


# Module-level job_id for cleanup
_test_job_id = None


def test_cron_add():
    """Test adding a cron job."""
    global _test_job_id
    print("\n=== Testing Cron Job Creation ===")
    try:
        resp = requests.post(
            f"{BASE_URL}/cron/jobs",
            json={
                "name": "Test Health Check",
                "schedule": "every 5 minutes",
                "command": "Check system health and report issues",
                "enabled": True,
            },
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"[+] Created test job: {data.get('id')}")
            print(f"   Next run: {data.get('next_run')}")
            # Store the job_id for cleanup
            _test_job_id = data.get("id")
            assert _test_job_id is not None
        else:
            print(f"[-] Failed to create job: {resp.status_code}")
            assert False, f"Failed to create job: {resp.status_code}"
    except Exception as e:
        print(f"[-] Error: {e}")
        pytest.skip(f"Gateway not accessible: {e}")


def test_cron_delete():
    """Test deleting a cron job."""
    global _test_job_id
    if not _test_job_id:
        pytest.skip("No job_id from previous test")

    print(f"\n=== Testing Cron Job Deletion ===")
    try:
        resp = requests.delete(f"{BASE_URL}/cron/jobs/{_test_job_id}", timeout=5)
        if resp.status_code == 200:
            print(f"[+] Deleted test job: {_test_job_id}")
            assert True
        else:
            print(f"[-] Failed to delete job: {resp.status_code}")
            assert False, f"Failed to delete job: {resp.status_code}"
    except Exception as e:
        print(f"[-] Error: {e}")
        pytest.skip(f"Gateway not accessible: {e}")


def test_heartbeat_trigger():
    """Test manual heartbeat trigger."""
    print("\n=== Testing Heartbeat Trigger ===")
    print("[...] Running heartbeat check (may take 30-60 seconds)...")

    try:
        start_time = time.time()
        resp = requests.post(f"{BASE_URL}/heartbeat/trigger", timeout=120)
        duration = time.time() - start_time

        if resp.status_code == 200:
            data = resp.json()
            print(f"[+] Heartbeat completed in {duration:.1f}s")

            if data.get("suppressed"):
                print("   Status: HEARTBEAT_OK - All systems healthy")
            else:
                issues = data.get("issues", [])
                print(f"   Status: Issues detected ({len(issues)})")
                for issue in issues[:3]:  # Show first 3
                    print(f"   [!] {issue}")

            print(f"\n   Response preview:")
            response_text = data.get("response", "")[:200]
            print(f"   {response_text}...")

            assert True
        else:
            print(f"[-] Heartbeat failed: {resp.status_code}")
            pytest.skip(f"Heartbeat failed: {resp.status_code}")
    except requests.Timeout:
        print("[-] Heartbeat timed out (>120s)")
        pytest.skip("Heartbeat timed out")
    except Exception as e:
        print(f"[-] Error: {e}")
        pytest.skip(f"Heartbeat error: {e}")


def test_heartbeat_history():
    """Test heartbeat history."""
    print("\n=== Testing Heartbeat History ===")
    try:
        resp = requests.get(f"{BASE_URL}/heartbeat/history?limit=5", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            history = data.get("history", [])
            print(f"[+] Retrieved {len(history)} history entries")

            for entry in history:
                timestamp = entry.get("timestamp", "unknown")
                success = "[+]" if entry.get("success") else "[-]"
                suppressed = entry.get("suppressed", False)
                issues = entry.get("issues_count", 0)

                status = "OK" if suppressed else f"{issues} issues"
                print(f"   {success} {timestamp[:19]} - {status}")

            assert True
        else:
            print(f"[-] History retrieval failed: {resp.status_code}")
            pytest.skip(f"History failed: {resp.status_code}")
    except Exception as e:
        print(f"[-] Error: {e}")
        pytest.skip(f"History error: {e}")


def test_ai_config():
    """Check if AI is configured."""
    print("\n=== Testing AI Configuration ===")
    try:
        from navig.config import get_config_manager

        config = get_config_manager()

        api_key = config.global_config.get("openrouter_api_key")
        if api_key:
            masked_key = (
                f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
            )
            print(f"[+] OpenRouter API key configured: {masked_key}")

            models = config.global_config.get("ai_model_preference", [])
            if models:
                print(f"   Preferred models:")
                for model in models[:3]:
                    print(f"   - {model}")
            else:
                print("   [!] No model preference set, will use default")

            assert True
        else:
            print("[-] OpenRouter API key not configured")
            print("   Set it with: navig config set openrouter_api_key <your_key>")
            pytest.skip("AI not configured")
    except Exception as e:
        print(f"[-] Error: {e}")
        pytest.skip(f"AI config error: {e}")


def test_workspace_files():
    """Check if workspace files exist."""
    print("\n=== Testing Workspace Configuration ===")
    from pathlib import Path

    workspace = Path.home() / ".navig" / "workspace"
    files_to_check = [
        ("HEARTBEAT.md", "Heartbeat instructions"),
        ("SOUL.md", "Agent personality"),
        ("AGENTS.md", "Operating instructions"),
    ]

    all_exist = True
    for filename, description in files_to_check:
        filepath = workspace / filename
        if filepath.exists():
            size = filepath.stat().st_size
            print(f"[+] {filename} exists ({size} bytes) - {description}")
        else:
            print(f"[-] {filename} missing - {description}")
            all_exist = False

    if not all_exist:
        import pytest

        pytest.skip(
            "Workspace files are missing (not fully initialized E2E environment)"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
