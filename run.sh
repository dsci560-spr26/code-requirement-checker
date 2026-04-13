#!/bin/bash
# Code-Requirement Checker — Quick Start Script
# Usage: ./run.sh

set -e

echo "🚀 Code-Requirement Checker — Starting..."
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Please install Python 3.10+"
    exit 1
fi

# Backend setup
cd backend

if [ ! -f .env ]; then
    echo "❌ No .env file found in backend/. Please create one with DEEPSEEK_API_KEY=..."
    exit 1
fi

# Install deps
echo "📦 Installing backend dependencies..."
pip install -r requirements.txt -q

# Start backend in background
echo "🔧 Starting FastAPI backend on port 8000..."
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

cd ..

# Start frontend
echo "🌐 Starting frontend on port 3000..."
cd frontend
python3 -m http.server 3000 &
FRONTEND_PID=$!

cd ..

echo ""
echo "✅ System is running!"
echo "   Frontend: http://localhost:3000"
echo "   Backend:  http://localhost:8000"
echo "   API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers."

# Trap to kill both on exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo ''; echo 'Servers stopped.'" EXIT

wait
