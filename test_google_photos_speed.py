import os
import sys
import time

import pytest
import requests

# Test URL - replace with an actual Google Photos sharing URL
TEST_URL = os.environ.get("GOOGLE_PHOTOS_TEST_URL", "")


@pytest.mark.skipif(not TEST_URL, reason="Set GOOGLE_PHOTOS_TEST_URL to a real Google Photos URL to run this benchmark.")
def test_extraction_speed():
    """Test the extraction speed multiple times for a configured live Google Photos URL."""
    times = []
    for i in range(3):
        start = time.time()
        try:
            response = requests.get(
                "http://127.0.0.1:5020/extract-google-photos-url",
                params={"url": TEST_URL},
                timeout=60,
            )
            elapsed = time.time() - start
            times.append(elapsed)
            print(f"Iteration {i + 1}: {elapsed:.2f}s - Status: {response.status_code}")
            if response.status_code == 200:
                print(f"  Result: {response.text[:100]}...")
            else:
                print(f"  Error: {response.text}")
        except Exception as exc:
            elapsed = time.time() - start
            times.append(elapsed)
            print(f"Iteration {i + 1}: {elapsed:.2f}s - Error: {exc}")

        if i < 2:
            time.sleep(1)

    if times:
        avg = sum(times) / len(times)
        print(f"\nAverage time: {avg:.2f}s")
        print(f"Min: {min(times):.2f}s, Max: {max(times):.2f}s")

    return times


if __name__ == "__main__":
    if len(sys.argv) > 1:
        TEST_URL = sys.argv[1]

    print(f"Testing Google Photos extraction speed for: {TEST_URL}")
    print("=" * 60)
    test_extraction_speed()
