# Agent Goal Planning Guide

Autonomous goal decomposition and execution tracking for the NAVIG agent.

## Overview

The goal planning system enables the NAVIG agent to accept high-level objectives and autonomously break them down into executable subtasks with dependency tracking and progress monitoring.

## Concepts

### Goals

A **goal** is a high-level objective the agent should achieve. Examples:
- "Deploy application to production"
- "Optimize database performance"
- "Migrate users to new authentication system"

### Subtasks

Goals are decomposed into **subtasks** - specific actionable steps:
- Check deployment prerequisites
- Run database migrations
- Update configuration files
- Restart services

### Dependencies

Subtasks can depend on other subtasks completing first:
```
Subtask 1: Backup database
Subtask 2: Run migrations (depends on 1)
Subtask 3: Verify schema (depends on 2)
```

### States

**Goal States:**
- `PENDING` - Created, awaiting decomposition
- `DECOMPOSING` - AI breaking down into subtasks
- `IN_PROGRESS` - Executing subtasks
- `BLOCKED` - Waiting on dependency or manual intervention
- `COMPLETED` - All subtasks done
- `FAILED` - Cannot proceed
- `CANCELLED` - Manually stopped

**Subtask States:**
- `PENDING` - Not started
- `IN_PROGRESS` - Currently executing
- `COMPLETED` - Successfully done
- `FAILED` - Execution failed
- `SKIPPED` - Skipped due to conditions

## CLI Usage

### Add a Goal

```bash
navig agent goal add --desc "Deploy app to production"
```

**Output:**
```
✓ Goal added: e78423a1

  Description: Deploy app to production
  ID: e78423a1

ℹ The agent will decompose this goal into subtasks
Check progress with: navig agent goal status --id e78423a1
```

### List Goals

```bash
navig agent goal list
```

**Output:**
```
ℹ Goals (3)

  IN_PROGRESS Deploy app to production
    ID: e78423a1
    Progress: 60%
    Subtasks: 5
    Created: 2026-02-06 10:30

  COMPLETED Optimize database performance
    ID: f9b2c4d5
    Progress: 100%
    Subtasks: 3
    Created: 2026-02-05 14:20

  PENDING Migrate authentication system
    ID: a3c7e8f1
    Progress: 0%
    Subtasks: 0
    Created: 2026-02-06 11:00
```

### View Goal Details

```bash
navig agent goal status --id e78423a1
```

**Output:**
```
ℹ Goal: Deploy app to production

  ID: e78423a1
  State: in_progress
  Progress: 60%
  Created: 2026-02-06 10:30
  Started: 2026-02-06 10:32

  Subtasks (5):
    ✅ Backup database
       Command: navig backup create production
    ✅ Run migrations
       Command: navig db migrate
       Depends on: e78423a1-1
    ✅ Update configuration
       Command: navig config update production
    🔄 Restart services
       Command: navig service restart app
       Depends on: e78423a1-3
    ⏳ Verify deployment
       Command: navig health check --full
       Depends on: e78423a1-4
```

### Cancel a Goal

```bash
navig agent goal cancel --id e78423a1
```

## Goal Lifecycle

### 1. Creation

```bash
navig agent goal add --desc "High-level objective"
```

Goal created with state `PENDING`.

### 2. Decomposition

Agent Brain analyzes the goal and breaks it into subtasks:

```python
# Automatic decomposition (future - requires Brain integration)
goal = planner.get_goal(goal_id)
subtasks = brain.decompose_goal(goal.description)
planner.decompose_goal(goal_id, subtasks)
```

**Manual decomposition** (current):
```python
from navig.agent.goals import GoalPlanner

planner = GoalPlanner()
subtasks = [
    {
        'description': 'Backup database',
        'command': 'navig backup create production',
        'dependencies': []
    },
    {
        'description': 'Run migrations',
        'command': 'navig db migrate',
        'dependencies': ['goal_id-1']  # Depends on backup
    },
    {
        'description': 'Restart services',
        'command': 'navig service restart app',
        'dependencies': ['goal_id-2']  # Depends on migrations
    }
]

planner.decompose_goal(goal_id, subtasks)
```

### 3. Execution

Start goal execution:

```bash
navig agent goal start --id e78423a1
```

Agent's Heart component periodically checks for next executable subtask:

```python
# In Heart's execution loop
next_task = planner.get_next_subtask(goal_id)
if next_task and all_dependencies_met(next_task):
    result = execute_subtask(next_task)
    if result.success:
        planner.complete_subtask(goal_id, next_task.id, result.output)
    else:
        planner.fail_subtask(goal_id, next_task.id, result.error)
```

### 4. Monitoring

Check progress anytime:

```bash
navig agent goal status --id e78423a1
```

Progress automatically calculated:
```python
completed = count(subtask for subtask in goal.subtasks if subtask.state == COMPLETED)
progress = completed / total_subtasks  # 0.0 to 1.0
```

### 5. Completion

When all subtasks complete, goal state → `COMPLETED`.

## Integration

### With Heart Orchestrator

```python
# In Heart._heartbeat_loop()
async def _heartbeat_loop(self):
    while True:
        await asyncio.sleep(self.config.heartbeat_interval)
        
        # Check goals
        if self.config.goals_enabled:
            await self._process_goals()

async def _process_goals(self):
    from navig.agent.goals import GoalPlanner, GoalState
    
    planner = GoalPlanner()
    in_progress_goals = planner.list_goals(state=GoalState.IN_PROGRESS)
    
    for goal in in_progress_goals:
        next_task = planner.get_next_subtask(goal.id)
        if next_task:
            await self._execute_subtask(goal.id, next_task)
```

### With Brain AI

```python
# Goal decomposition using AI
async def decompose_goal(self, goal_description: str) -> List[Dict]:
    prompt = f"""
    Break down this goal into specific, actionable subtasks:
    
    Goal: {goal_description}
    
    For each subtask, provide:
    1. Description (what to do)
    2. Command (navig CLI command)
    3. Dependencies (which subtasks must complete first)
    
    Return as JSON array.
    """
    
    response = await self.brain.query(prompt)
    return json.loads(response)
```

### With Hands Execution

```python
# Safe command execution through Hands
async def _execute_subtask(self, goal_id: str, subtask: Subtask):
    if not subtask.command:
        # No command, mark as completed
        planner.complete_subtask(goal_id, subtask.id, "No command")
        return
    
    # Execute through Hands component for safety checks
    result = await self.hands.execute_command(
        subtask.command,
        approval_required=True if 'delete' in subtask.command or 'drop' in subtask.command else False
    )
    
    if result.success:
        planner.complete_subtask(goal_id, subtask.id, result.output)
    else:
        planner.fail_subtask(goal_id, subtask.id, result.error)
```

## Storage

Goals persisted to `~/.navig/workspace/goals.json`:

```json
{
  "goals": [
    {
      "id": "e78423a1",
      "description": "Deploy app to production",
      "state": "in_progress",
      "subtasks": [
        {
          "id": "e78423a1-1",
          "description": "Backup database",
          "command": "navig backup create production",
          "dependencies": [],
          "state": "completed",
          "created_at": "2026-02-06T10:32:00",
          "completed_at": "2026-02-06T10:33:15",
          "result": "Backup created: production-20260206.sql"
        },
        {
          "id": "e78423a1-2",
          "description": "Run migrations",
          "command": "navig db migrate",
          "dependencies": ["e78423a1-1"],
          "state": "in_progress",
          "started_at": "2026-02-06T10:33:20"
        }
      ],
      "created_at": "2026-02-06T10:30:00",
      "started_at": "2026-02-06T10:32:00",
      "progress": 0.2,
      "metadata": {}
    }
  ],
  "updated_at": "2026-02-06T10:33:20"
}
```

## API Reference

### GoalPlanner

```python
from navig.agent.goals import GoalPlanner, GoalState

planner = GoalPlanner()

# Add goal
goal_id = planner.add_goal("Deploy app to production", metadata={'env': 'prod'})

# Decompose goal
subtasks = [
    {'description': 'Task 1', 'command': 'cmd1', 'dependencies': []},
    {'description': 'Task 2', 'command': 'cmd2', 'dependencies': ['goal-1']}
]
planner.decompose_goal(goal_id, subtasks)

# Start execution
planner.start_goal(goal_id)

# Get next executable subtask
next_task = planner.get_next_subtask(goal_id)

# Complete subtask
planner.complete_subtask(goal_id, next_task.id, result="Success")

# Fail subtask
planner.fail_subtask(goal_id, next_task.id, error="Command failed")

# Cancel goal
planner.cancel_goal(goal_id)

# List goals
all_goals = planner.list_goals()
pending = planner.list_goals(state=GoalState.PENDING)

# Get specific goal
goal = planner.get_goal(goal_id)
print(f"Progress: {goal.progress * 100}%")
```

## Advanced Usage

### Complex Dependencies

Multiple dependencies:

```python
subtasks = [
    {'description': 'Task A', 'dependencies': []},
    {'description': 'Task B', 'dependencies': []},
    {'description': 'Task C', 'dependencies': ['task-A', 'task-B']},  # Wait for both
]
```

The agent will only execute Task C after both A and B complete.

### Conditional Execution

Use metadata for conditions:

```python
goal = planner.get_goal(goal_id)
if goal.metadata.get('environment') == 'production':
    # Extra validation subtasks
    extra_tasks = [...]
    goal.subtasks.extend(extra_tasks)
```

### Progress Tracking

```python
# Monitor progress
goal = planner.get_goal(goal_id)
print(f"Progress: {goal.progress * 100:.1f}%")
print(f"Completed: {sum(1 for st in goal.subtasks if st.state == SubtaskState.COMPLETED)}/{len(goal.subtasks)}")

# Estimate time remaining
if goal.started_at:
    elapsed = datetime.now() - goal.started_at
    estimated_total = elapsed / goal.progress if goal.progress > 0 else None
    remaining = estimated_total - elapsed if estimated_total else None
```

### Parallel Execution

Independent subtasks (no dependencies) can run in parallel:

```python
async def execute_parallel_subtasks(goal_id):
    goal = planner.get_goal(goal_id)
    
    # Find all subtasks with no dependencies and not completed
    ready_tasks = [
        st for st in goal.subtasks 
        if st.state == SubtaskState.PENDING and not st.dependencies
    ]
    
    # Execute in parallel
    results = await asyncio.gather(*[
        execute_subtask(goal_id, task)
        for task in ready_tasks
    ])
```

## Examples

### Example 1: Database Migration

```bash
navig agent goal add --desc "Migrate database schema"
```

Decomposed into:
1. Backup current database
2. Test migration on staging
3. Run migration on production (depends on 2)
4. Verify schema integrity (depends on 3)
5. Update application config (depends on 4)

### Example 2: Deployment Pipeline

```bash
navig agent goal add --desc "Deploy v2.0 to production"
```

Decomposed into:
1. Run test suite
2. Build Docker image (depends on 1)
3. Push to registry (depends on 2)
4. Update k8s manifests (depends on 3)
5. Apply deployment (depends on 4)
6. Run smoke tests (depends on 5)
7. Update documentation (depends on 6)

### Example 3: System Maintenance

```bash
navig agent goal add --desc "Perform monthly maintenance"
```

Decomposed into:
1. Create system backup
2. Update packages (depends on 1)
3. Restart services (depends on 2)
4. Run health checks (depends on 3)
5. Clean old logs (depends on 4)
6. Generate maintenance report (depends on 5)

## Troubleshooting

### Goal Stuck in PENDING

Goal hasn't been decomposed yet. Either:
- Wait for AI decomposition (requires Brain integration)
- Manually decompose using API

### Goal Stuck in BLOCKED

Check subtask states:

```bash
navig agent goal status --id <goal-id>
```

Look for:
- FAILED subtasks → Fix and retry
- Unmet dependencies → Check why dependency didn't complete

### Subtask Won't Execute

Check:
1. Dependencies completed?
2. Command valid?
3. Agent running?
4. Hands component enabled?

### Progress Not Updating

```bash
# Check goal state
navig agent goal status --id <goal-id>

# Check agent status
navig agent status

# Check logs
navig agent logs --level error
```

## Best Practices

### 1. Clear Goal Descriptions

❌ Bad: "Fix the app"
✅ Good: "Deploy application version 2.1.0 to production environment"

### 2. Granular Subtasks

Break down into small, testable steps:
- Each subtask should take < 5 minutes
- Each subtask should have clear success criteria
- Avoid combining unrelated operations

### 3. Explicit Dependencies

Always specify dependencies:
```python
{'description': 'Restart services', 'dependencies': ['backup-task-id', 'migration-task-id']}
```

### 4. Monitor Progress

```bash
# Set up monitoring
watch -n 5 'navig agent goal status --id <goal-id>'

# Or use agent's proactive notifications
navig agent config --set brain.proactive true
```

### 5. Use Metadata

Store context in metadata:
```python
planner.add_goal(
    "Deploy app",
    metadata={
        'version': '2.1.0',
        'environment': 'production',
        'rollback_on_failure': True,
        'notify_team': True
    }
)
```

## Future Enhancements

Planned improvements:

- **AI Decomposition** - Automatic goal breakdown using Brain
- **Parallel Execution** - Run independent subtasks simultaneously
- **Rollback Support** - Automatic rollback on failure
- **Approval Gates** - Human approval required for critical steps
- **Time Estimates** - Predict completion time
- **Resource Planning** - Check resource requirements before execution
- **Goal Templates** - Pre-defined goal patterns for common tasks

## See Also

- [Agent Mode Overview](AGENT_MODE.md)
- [Agent Self-Healing](AGENT_SELF_HEALING.md)
- [Agent Service Installation](AGENT_SERVICE.md)
- [Troubleshooting](troubleshooting.md)


