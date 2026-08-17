import sys

sys.stdin.buffer.readline()
sys.stdout.buffer.write(b"not-json\n")
sys.stdout.buffer.flush()
