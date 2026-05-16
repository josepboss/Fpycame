#!/bin/bash
cd "$(dirname "$0")/.."
uvicorn admin.main:app --host 0.0.0.0 --port 7430