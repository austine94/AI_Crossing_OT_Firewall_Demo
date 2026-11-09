# BoundaryLine troubleshooting

## Ollama immediately reloads a model after it is stopped

Stopping Docker or sending an Ollama unload request does not stop an unrelated host process
that continues to call Ollama. A client making a new request will immediately load its selected
model again.

On 24 October 2026, the known cause was a detached loop from a separate project:

```text
Working directory: /Users/localadmin/Documents/BBC/Intelligent-Placement-Optimisation-main/demo
Command: python3 run_demo.py --loop --interval 30 --model gemma4:26b-a4b-it-q4_K_M
Child: mcp_server.py
```

The loop had been re-parented to `launchd` and survived closing its original terminal. Every 30
seconds it sent more `/api/chat` requests to `127.0.0.1:11434`, so stopping Docker and unloading
the model did not solve the problem.

### Complete shutdown checklist

1. Stop the owning demo loop first. Find the exact process and verify its command and working
   directory before terminating it:

   ```sh
   pgrep -alf '[r]un_demo.py|[m]cp_server.py'
   lsof -a -p <run-demo-pid> -d cwd
   kill -TERM <run-demo-pid>
   ```

2. Confirm that both the parent loop and its child have exited:

   ```sh
   pgrep -alf '[r]un_demo.py|[m]cp_server.py'
   ```

   No matching demo process should remain. If a child remains, inspect it before terminating that
   exact PID; do not use a broad `pkill python` command.

3. Unload the model after its caller has stopped:

   ```sh
   curl --fail --silent --show-error \
     --request POST http://127.0.0.1:11434/api/generate \
     --header 'Content-Type: application/json' \
     --data '{"model":"gemma4:26b-a4b-it-q4_K_M","keep_alive":0}'
   ```

4. Verify that Ollama has no resident runner:

   ```sh
   ollama ps
   pgrep -alf 'llama-server'
   ```

   `ollama ps` should show only its headings and `pgrep` should return no `llama-server` process.

5. For BoundaryLine itself, stop its containers without deleting evidence volumes:

   ```sh
   docker compose stop
   ```

### Diagnosis tips

- Use `lsof -nP -iTCP:11434` to identify the process connected to Ollama.
- Check `~/.ollama/logs/server.log` for repeated `/api/chat` requests.
- Docker being open is not evidence that Docker caused the load. Identify the actual client PID
  connected to port 11434.
- An unload request cannot remain effective while another process is still issuing model requests.

