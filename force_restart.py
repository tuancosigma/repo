"""Script to help force restart Flask server and verify routes."""
import subprocess
import sys
import os
import time
import requests

def kill_flask_processes():
    """Kill all Python processes running Flask."""
    print("Checking for running Flask processes...")
    try:
        if sys.platform == 'win32':
            # Windows
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe'], 
                                  capture_output=True, text=True)
            if 'python.exe' in result.stdout:
                print("Found Python processes. Please manually stop Flask server:")
                print("  1. Find the terminal running Flask server")
                print("  2. Press Ctrl+C to stop it")
                print("  3. Wait for it to fully stop")
        else:
            # Linux/Mac
            result = subprocess.run(['pgrep', '-f', 'python.*app.py'], 
                                  capture_output=True, text=True)
            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                print(f"Found Flask processes: {pids}")
                for pid in pids:
                    try:
                        subprocess.run(['kill', pid])
                        print(f"Killed process {pid}")
                    except:
                        pass
    except Exception as e:
        print(f"Error checking processes: {e}")

def check_port_5000():
    """Check if port 5000 is in use."""
    try:
        response = requests.get("http://localhost:5000/", timeout=2)
        return True
    except:
        return False

def verify_routes_in_code():
    """Verify routes are registered in code."""
    print("\nVerifying routes in code...")
    try:
        from app import app
        routes = [(r.rule, r.endpoint) for r in app.url_map.iter_rules() 
                 if not r.rule.startswith('/static')]
        print(f"Found {len(routes)} routes in code:")
        for rule, endpoint in sorted(routes):
            print(f"  {rule:40} -> {endpoint}")
        return len(routes) == 15
    except Exception as e:
        print(f"Error importing app: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_routes_on_server():
    """Test routes on running server."""
    print("\nTesting routes on server...")
    test_routes = [
        ("/api/stats", {"period": "weekly"}),
        ("/api/alerts/domains/count", {"period": "weekly"}),
        ("/api/hwid/list", {"period": "weekly", "limit": 10}),
    ]
    
    results = []
    for route, params in test_routes:
        try:
            response = requests.get(f"http://localhost:5000{route}", params=params, timeout=5)
            status = "OK" if response.status_code < 400 else f"FAIL ({response.status_code})"
            results.append((route, status, response.status_code))
            print(f"  {route:40} -> {status}")
        except Exception as e:
            results.append((route, f"ERROR: {e}", 0))
            print(f"  {route:40} -> ERROR: {e}")
    
    return results

if __name__ == "__main__":
    print("="*60)
    print("Flask Server Restart Helper")
    print("="*60)
    
    # Step 1: Verify routes in code
    if not verify_routes_in_code():
        print("\n[ERROR] Routes not found in code. Check app.py for errors.")
        sys.exit(1)
    
    # Step 2: Check if server is running
    print("\nChecking if server is running...")
    if check_port_5000():
        print("[WARNING] Server is running on port 5000")
        print("\nIMPORTANT: You need to STOP the server first!")
        print("Steps:")
        print("  1. Find the terminal/command prompt running Flask server")
        print("  2. Press Ctrl+C to stop it")
        print("  3. Wait until you see 'Server stopped' message")
        print("  4. Then run: python app.py")
        print("\nAfter restarting, run this script again to verify routes.")
    else:
        print("[OK] No server running on port 5000")
        print("\nTo start server:")
        print("  python app.py")
        print("\nAfter starting, run this script again to verify routes.")
    
    # Step 3: Test routes if server is running
    if check_port_5000():
        print("\n" + "="*60)
        test_routes_on_server()
    
    print("\n" + "="*60)
    print("Done!")
