"""Per-site recipes — deterministic "how to do X on site Y" flows.

The reliable, no-LLM half of the "2 ways" model: a recipe knows the stable way to
do a task on a specific site (e.g. Gmail compose via its deep-link URL). When no
recipe exists, the AI (`navig do`) drives the site instead.
"""
