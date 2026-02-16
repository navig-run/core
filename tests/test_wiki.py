"""
Tests for Wiki Module

Tests the wiki management functionality including:
- Wiki initialization
- Page CRUD operations
- Inbox processing
- Wiki link resolution
- Search functionality
"""

import pytest
from pathlib import Path
from datetime import datetime
import tempfile
import shutil
import yaml

from navig.commands.wiki import (
    init_wiki,
    ensure_wiki_initialized,
    list_wiki_pages,
    search_wiki,
    resolve_wiki_link,
    find_broken_links,
    categorize_content,
    process_inbox_item,
    update_index,
    get_wiki_config,
    WIKI_STRUCTURE,
)


@pytest.fixture
def temp_wiki():
    """Create a temporary wiki directory for testing."""
    temp_dir = Path(tempfile.mkdtemp())
    wiki_path = temp_dir / "wiki"
    yield wiki_path
    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestWikiInitialization:
    """Test wiki initialization."""
    
    def test_init_creates_structure(self, temp_wiki):
        """Test that init creates the correct folder structure."""
        assert init_wiki(temp_wiki) is True
        
        # Check main folders exist
        assert (temp_wiki / "inbox").is_dir()
        assert (temp_wiki / ".meta").is_dir()
        assert (temp_wiki / "knowledge").is_dir()
        assert (temp_wiki / "technical").is_dir()
        assert (temp_wiki / "hub").is_dir()
        assert (temp_wiki / "external").is_dir()
        assert (temp_wiki / "archive").is_dir()
    
    def test_init_creates_subfolders(self, temp_wiki):
        """Test that init creates subfolders."""
        init_wiki(temp_wiki)
        
        # Knowledge subfolders
        assert (temp_wiki / "knowledge" / "concepts").is_dir()
        assert (temp_wiki / "knowledge" / "domain").is_dir()
        assert (temp_wiki / "knowledge" / "guides").is_dir()
        assert (temp_wiki / "knowledge" / "resources").is_dir()
        
        # Technical subfolders
        assert (temp_wiki / "technical" / "architecture").is_dir()
        assert (temp_wiki / "technical" / "api").is_dir()
        assert (temp_wiki / "technical" / "database").is_dir()
        assert (temp_wiki / "technical" / "decisions").is_dir()
        
        # Hub subfolders
        assert (temp_wiki / "hub" / "roadmap").is_dir()
        assert (temp_wiki / "hub" / "planning").is_dir()
        assert (temp_wiki / "hub" / "tasks").is_dir()
    
    def test_init_creates_config(self, temp_wiki):
        """Test that init creates config.yaml."""
        init_wiki(temp_wiki)
        
        config_path = temp_wiki / ".meta" / "config.yaml"
        assert config_path.exists()
        
        # Check config is valid YAML
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert 'wiki' in config
        assert config['wiki']['version'] == "1.0"
    
    def test_init_creates_index(self, temp_wiki):
        """Test that init creates index.md."""
        init_wiki(temp_wiki)
        
        index_path = temp_wiki / ".meta" / "index.md"
        assert index_path.exists()
        
        content = index_path.read_text()
        assert "# Wiki Index" in content
    
    def test_init_creates_visibility(self, temp_wiki):
        """Test that init creates .visibility file."""
        init_wiki(temp_wiki)
        
        visibility_path = temp_wiki / "knowledge" / ".visibility"
        assert visibility_path.exists()
        
        content = visibility_path.read_text()
        assert "visibility:" in content
    
    def test_ensure_wiki_initialized(self, temp_wiki):
        """Test wiki initialization check."""
        assert ensure_wiki_initialized(temp_wiki) is False
        
        init_wiki(temp_wiki)
        assert ensure_wiki_initialized(temp_wiki) is True
    
    def test_init_no_overwrite_without_force(self, temp_wiki):
        """Test that init doesn't overwrite without force."""
        init_wiki(temp_wiki)
        
        # Modify config
        config_path = temp_wiki / ".meta" / "config.yaml"
        config_path.write_text("# Modified")
        
        # Init again without force
        result = init_wiki(temp_wiki, force=False)
        assert result is False
        
        # Config should be unchanged
        assert config_path.read_text() == "# Modified"
    
    def test_init_overwrites_with_force(self, temp_wiki):
        """Test that init overwrites with force."""
        init_wiki(temp_wiki)
        
        # Modify config
        config_path = temp_wiki / ".meta" / "config.yaml"
        config_path.write_text("# Modified")
        
        # Init again with force
        result = init_wiki(temp_wiki, force=True)
        assert result is True
        
        # Config should be restored
        assert "# Modified" not in config_path.read_text()


class TestWikiPages:
    """Test wiki page operations."""
    
    def test_list_empty_wiki(self, temp_wiki):
        """Test listing pages in empty wiki."""
        init_wiki(temp_wiki)
        pages = list_wiki_pages(temp_wiki)
        
        # Should only find index.md and glossary.md in .meta (which are excluded)
        assert len(pages) == 0
    
    def test_list_with_pages(self, temp_wiki):
        """Test listing pages with content."""
        init_wiki(temp_wiki)
        
        # Create some pages
        (temp_wiki / "knowledge" / "concepts" / "test-concept.md").write_text("# Test Concept\n\nContent here.")
        (temp_wiki / "technical" / "api" / "endpoints.md").write_text("# API Endpoints\n\nList of endpoints.")
        
        pages = list_wiki_pages(temp_wiki)
        
        assert len(pages) == 2
        
        # Check page info
        paths = [p["path"] for p in pages]
        assert "knowledge/concepts/test-concept.md" in paths
        assert "technical/api/endpoints.md" in paths
    
    def test_list_extracts_title(self, temp_wiki):
        """Test that list extracts title from heading."""
        init_wiki(temp_wiki)
        
        (temp_wiki / "knowledge" / "concepts" / "test.md").write_text("# My Custom Title\n\nContent here.")
        
        pages = list_wiki_pages(temp_wiki)
        
        assert len(pages) == 1
        assert pages[0]["title"] == "My Custom Title"
    
    def test_list_excludes_hidden(self, temp_wiki):
        """Test that list excludes hidden folders."""
        init_wiki(temp_wiki)
        
        # Create pages in hidden folder (.meta)
        (temp_wiki / ".meta" / "test.md").write_text("# Hidden\n\nShould not appear.")
        
        # Create visible page
        (temp_wiki / "knowledge" / "concepts" / "visible.md").write_text("# Visible\n\nShould appear.")
        
        pages = list_wiki_pages(temp_wiki)
        
        assert len(pages) == 1
        assert pages[0]["name"] == "visible"
    
    def test_list_by_folder(self, temp_wiki):
        """Test listing pages in specific folder."""
        init_wiki(temp_wiki)
        
        (temp_wiki / "knowledge" / "concepts" / "a.md").write_text("# A")
        (temp_wiki / "technical" / "api" / "b.md").write_text("# B")
        
        # List only knowledge
        pages = list_wiki_pages(temp_wiki, folder="knowledge")
        
        assert len(pages) == 1
        assert pages[0]["name"] == "a"


class TestWikiSearch:
    """Test wiki search functionality."""
    
    def test_search_finds_match(self, temp_wiki):
        """Test basic search functionality."""
        init_wiki(temp_wiki)
        
        (temp_wiki / "knowledge" / "concepts" / "python.md").write_text(
            "# Python Programming\n\nPython is a great language."
        )
        (temp_wiki / "knowledge" / "concepts" / "java.md").write_text(
            "# Java Programming\n\nJava is another language."
        )
        
        results = search_wiki(temp_wiki, "Python")
        
        assert len(results) == 1
        assert results[0]["path"] == "knowledge/concepts/python.md"
    
    def test_search_case_insensitive(self, temp_wiki):
        """Test case-insensitive search."""
        init_wiki(temp_wiki)
        
        (temp_wiki / "knowledge" / "concepts" / "test.md").write_text("# API Documentation")
        
        results = search_wiki(temp_wiki, "api")
        assert len(results) == 1
        
        results = search_wiki(temp_wiki, "API")
        assert len(results) == 1
    
    def test_search_returns_context(self, temp_wiki):
        """Test that search returns context around match."""
        init_wiki(temp_wiki)
        
        (temp_wiki / "knowledge" / "concepts" / "test.md").write_text(
            "Some text before. The important keyword is here. Some text after."
        )
        
        results = search_wiki(temp_wiki, "keyword")
        
        assert len(results) == 1
        assert "keyword" in results[0]["context"]
    
    def test_search_no_results(self, temp_wiki):
        """Test search with no matches."""
        init_wiki(temp_wiki)
        
        (temp_wiki / "knowledge" / "concepts" / "test.md").write_text("# Test")
        
        results = search_wiki(temp_wiki, "nonexistent")
        assert len(results) == 0


class TestWikiLinks:
    """Test wiki link resolution."""
    
    def test_resolve_exact_path(self, temp_wiki):
        """Test resolving exact path."""
        init_wiki(temp_wiki)
        
        page_path = temp_wiki / "knowledge" / "concepts" / "overview.md"
        page_path.write_text("# Overview")
        
        resolved = resolve_wiki_link(temp_wiki, "knowledge/concepts/overview")
        
        assert resolved == page_path
    
    def test_resolve_fuzzy(self, temp_wiki):
        """Test fuzzy resolution by name."""
        init_wiki(temp_wiki)
        
        page_path = temp_wiki / "technical" / "api" / "auth.md"
        page_path.write_text("# Auth API")
        
        resolved = resolve_wiki_link(temp_wiki, "auth")
        
        assert resolved == page_path
    
    def test_resolve_with_display_text(self, temp_wiki):
        """Test resolving link with display text."""
        init_wiki(temp_wiki)
        
        page_path = temp_wiki / "knowledge" / "concepts" / "intro.md"
        page_path.write_text("# Introduction")
        
        resolved = resolve_wiki_link(temp_wiki, "knowledge/concepts/intro|Introduction")
        
        assert resolved == page_path
    
    def test_resolve_not_found(self, temp_wiki):
        """Test resolution when page doesn't exist."""
        init_wiki(temp_wiki)
        
        resolved = resolve_wiki_link(temp_wiki, "nonexistent")
        
        assert resolved is None
    
    def test_find_broken_links(self, temp_wiki):
        """Test finding broken links."""
        init_wiki(temp_wiki)
        
        # Create page with broken link
        (temp_wiki / "knowledge" / "concepts" / "test.md").write_text(
            "# Test\n\nSee [[nonexistent]] for more info."
        )
        
        broken = find_broken_links(temp_wiki)
        
        assert len(broken) == 1
        assert broken[0]["link"] == "nonexistent"
    
    def test_find_no_broken_links(self, temp_wiki):
        """Test when all links are valid."""
        init_wiki(temp_wiki)
        
        # Create target page
        (temp_wiki / "knowledge" / "concepts" / "target.md").write_text("# Target")
        
        # Create page with valid link
        (temp_wiki / "knowledge" / "concepts" / "source.md").write_text(
            "# Source\n\nSee [[target]] for more info."
        )
        
        broken = find_broken_links(temp_wiki)
        
        assert len(broken) == 0


class TestContentCategorization:
    """Test AI content categorization."""
    
    def test_categorize_technical_api(self):
        """Test categorizing API documentation."""
        content = "# API Endpoints\n\nGET /api/users returns a list of users."
        result = categorize_content(content, "api-docs.md")
        
        assert result.startswith("technical")
        assert "api" in result
    
    def test_categorize_technical_database(self):
        """Test categorizing database content."""
        content = "# Database Schema\n\nThe users table has the following columns..."
        result = categorize_content(content, "schema.md")
        
        assert result.startswith("technical")
        assert "database" in result
    
    def test_categorize_business(self):
        """Test categorizing business content."""
        content = "# Investor Pitch\n\nOur market opportunity and ROI projections..."
        result = categorize_content(content, "pitch.md")
        
        assert "external" in result or "business" in result
    
    def test_categorize_marketing(self):
        """Test categorizing marketing content."""
        content = "# Marketing Campaign\n\nSocial media strategy for Q4..."
        result = categorize_content(content, "campaign.md")
        
        assert "marketing" in result
    
    def test_categorize_hub_roadmap(self):
        """Test categorizing roadmap content."""
        content = "# Product Roadmap\n\nMilestone 1: MVP by Q2..."
        result = categorize_content(content, "roadmap.md")
        
        assert "hub" in result
        assert "roadmap" in result
    
    def test_categorize_hub_tasks(self):
        """Test categorizing task content."""
        content = "# Sprint Tasks\n\n- [ ] TODO: implement feature X"
        result = categorize_content(content, "tasks.md")
        
        assert "hub" in result
    
    def test_categorize_knowledge_guide(self):
        """Test categorizing guide content."""
        content = "# How to Get Started\n\nThis tutorial shows you how to..."
        result = categorize_content(content, "getting-started.md")
        
        assert "knowledge" in result
        assert "guides" in result
    
    def test_categorize_default(self):
        """Test default categorization for generic content."""
        content = "# Something\n\nJust some random content without keywords."
        result = categorize_content(content, "random.md")
        
        assert "knowledge" in result


class TestInboxProcessing:
    """Test inbox processing."""
    
    def test_process_inbox_item(self, temp_wiki):
        """Test processing a single inbox item."""
        init_wiki(temp_wiki)
        
        # Create inbox item
        inbox_path = temp_wiki / "inbox" / "api-docs.md"
        inbox_path.write_text("# API Documentation\n\nGET /api/users endpoint.")
        
        result = process_inbox_item(temp_wiki, "api-docs.md", auto_move=False)
        
        assert "error" not in result
        assert result["file"] == "api-docs.md"
        assert result["title"] == "API Documentation"
        assert "technical" in result["suggested_folder"]
    
    def test_process_inbox_item_auto_move(self, temp_wiki):
        """Test auto-moving inbox item."""
        init_wiki(temp_wiki)
        
        # Create inbox item
        inbox_path = temp_wiki / "inbox" / "api-docs.md"
        inbox_path.write_text("# API Docs\n\nEndpoint documentation.")
        
        result = process_inbox_item(temp_wiki, "api-docs.md", auto_move=True)
        
        assert "moved_to" in result
        assert not inbox_path.exists()
    
    def test_process_nonexistent_file(self, temp_wiki):
        """Test processing non-existent file."""
        init_wiki(temp_wiki)
        
        result = process_inbox_item(temp_wiki, "nonexistent.md")
        
        assert "error" in result


class TestUpdateIndex:
    """Test index updating."""
    
    def test_update_index_empty(self, temp_wiki):
        """Test updating index with no pages."""
        init_wiki(temp_wiki)
        update_index(temp_wiki)
        
        index_content = (temp_wiki / ".meta" / "index.md").read_text()
        
        assert "# Wiki Index" in index_content
        assert "Total Pages:" in index_content
    
    def test_update_index_with_pages(self, temp_wiki):
        """Test updating index with pages."""
        init_wiki(temp_wiki)
        
        (temp_wiki / "knowledge" / "concepts" / "test.md").write_text("# Test Page")
        (temp_wiki / "technical" / "api" / "endpoints.md").write_text("# Endpoints")
        
        update_index(temp_wiki)
        
        index_content = (temp_wiki / ".meta" / "index.md").read_text()
        
        assert "Knowledge" in index_content
        assert "Technical" in index_content
        assert "test" in index_content.lower()
        assert "endpoints" in index_content.lower()


class TestGetWikiConfig:
    """Test wiki configuration loading."""
    
    def test_get_config(self, temp_wiki):
        """Test loading wiki config."""
        init_wiki(temp_wiki)
        
        config = get_wiki_config(temp_wiki)
        
        assert "wiki" in config
        assert config["wiki"]["version"] == "1.0"
        assert config["wiki"]["link_style"] == "wiki"
    
    def test_get_config_nonexistent(self, temp_wiki):
        """Test loading config from non-existent wiki."""
        config = get_wiki_config(temp_wiki)
        
        assert config == {}
