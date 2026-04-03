#!/usr/bin/env node
const { spawn } = require('child_process');

const args = process.argv.slice(2);
const py = process.env.PYTHON || 'python3';

const child = spawn(py, ['-m', 'devchat.cli', ...args], { stdio: 'inherit' });

child.on('error', () => {
  console.error('DevChat requires Python 3.10+ in PATH.');
  console.error('Install Python, then run: pip install devchat-cli (or pip install -e . in this repo).');
  process.exit(1);
});

child.on('exit', (code) => process.exit(code ?? 0));
