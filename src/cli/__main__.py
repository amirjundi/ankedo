"""
CLI entry point for AnkEdo.
Usage: python -m src.cli [COMMAND]
Commands:
  db init
  agent run [--continuous] [--cycles N] [--stage STAGE]
  cases add ...
  accounts add ...
"""
import asyncio
import sys

import click
import structlog

from src.core.database import init_db
from src.core.logging_config import configure_logging

log = structlog.get_logger()


@click.group()
def main():
    """AnkEdo — AI-Powered Hate Speech Monitoring Agent"""
    configure_logging()


@main.group(name="db")
def db_group():
    """Database management commands"""
    pass


@db_group.command(name="init")
def db_init():
    """Initialize the SQLite database schema."""
    log.info("Initializing database schema...")
    try:
        asyncio.run(init_db())
        log.info("Database initialized successfully.")
    except Exception as e:
        log.exception("Failed to initialize database", error=str(e))
        sys.exit(1)


@main.group(name="agent")
def agent_group():
    """Agent execution commands"""
    pass


@agent_group.command(name="run")
@click.option("--continuous", is_flag=True, help="Run continuously in a loop")
@click.option("--cycles", type=int, default=1, help="Number of cycles to run if not continuous")
@click.option("--stage", type=str, help="Run only a specific pipeline stage")
def agent_run(continuous: bool, cycles: int, stage: str | None):
    """Start the monitoring agent."""
    log.info("Starting agent run", continuous=continuous, cycles=cycles, stage=stage)
    # TODO: Implement orchestration loop execution
    log.info("Agent run complete (stub)")


@main.group(name="cases")
def cases_group():
    """Case management commands"""
    pass


@cases_group.command(name="add")
@click.option("--group", required=True, help="Target group name")
@click.option("--seed", required=True, help="Comma-separated seed URLs")
@click.option("--keywords", required=True, help="Comma-separated keywords")
def cases_add(group: str, seed: str, keywords: str):
    """Register a new incident case."""
    log.info("Adding new case", group=group, seed=seed, keywords=keywords)
    
    async def _add():
        from src.core.database import get_session
        from src.core.case_manager import CaseManager
        from src.models.case import CaseSeverity
        
        async with get_session() as session:
            manager = CaseManager(session)
            await manager.create_case(
                target_group=group,
                seed_posts=[s.strip() for s in seed.split(",")],
                watch_keywords=[k.strip() for k in keywords.split(",")],
                severity=CaseSeverity.MEDIUM
            )
            
    asyncio.run(_add())
    log.info("Case added successfully")


@main.group(name="accounts")
def accounts_group():
    """Account management commands"""
    pass


@accounts_group.command(name="add")
@click.option("--platform", required=True, help="Platform (facebook, tiktok, instagram)")
@click.option("--username", required=True, help="Account username")
def accounts_add(platform: str, username: str):
    """Add a new worker account for the agent to use."""
    log.info("Adding new worker account", platform=platform, username=username)
    # TODO: Implement account registration
    log.info("Worker account added successfully (stub)")


if __name__ == "__main__":
    main()
