#!/usr/bin/env python3
"""
Test script to check if the app starts correctly
"""

print("Starting app test...")

try:
    print("1. Importing create_app...")
    from application import create_app
    print("   ✅ Import successful")
    
    print("2. Creating app instance...")
    app = create_app()
    print("   ✅ App created successfully")
    
    print("3. Starting Flask development server...")
    app.run(debug=True, host='127.0.0.1', port=5000)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
