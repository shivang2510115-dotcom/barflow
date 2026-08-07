"""Database handle. Falls back to a JSON-file mock when MONGO_URL is unset."""
import os
import logging

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

mongo_url = os.environ.get('MONGO_URL', 'mock')
if not mongo_url or mongo_url.startswith('mock') or mongo_url.startswith('local'):
    logger.info("Using local JSON file-based database mock...")
    from mock_db import MockMongoClient
    client = MockMongoClient(None)
    db = client[None]
else:
    logger.info("Connecting to remote MongoDB client...")
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ.get('DB_NAME', 'barflow')]
