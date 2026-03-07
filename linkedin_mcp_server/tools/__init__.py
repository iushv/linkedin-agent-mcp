# src/linkedin_mcp_server/tools/__init__.py
"""
LinkedIn scraping tools package.

This package contains the MCP tool implementations for LinkedIn data extraction.
Each tool module provides specific functionality for different LinkedIn entities
while sharing common error handling and driver management patterns.

Available Tools:
- Person tools: LinkedIn profile scraping and analysis
- Company tools: Company profile and information extraction
- Job tools: Job posting details and search functionality
- Post tools: Post creation, reposting, polls, and deletion
- Engagement tools: Reactions and comment interactions
- Messaging tools: Conversation reads and message sending
- Network tools: Invitation and connection management
- Feed tools: Feed browsing and analytics extraction

Architecture:
- FastMCP integration for MCP-compliant tool registration
- Shared error handling through centralized error_handler module
- Singleton driver pattern for session persistence
- Structured data return format for consistent MCP responses
"""
