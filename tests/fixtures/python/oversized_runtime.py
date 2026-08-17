import sys

sys.stdin.buffer.readline()
sys.stdout.buffer.write(b"x" * (1024 * 1024 + 1))
sys.stdout.buffer.flush()
