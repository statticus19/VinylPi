"""FFmpeg audio operations with bounded subprocesses."""
import math
import re
import subprocess


def run(command, timeout=600):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Audio operation failed: ' + result.stderr[-1200:])
    return result


def duration(path):
    value = float(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                      '-of', 'default=noprint_wrappers=1:nokey=1', str(path)], 20).stdout.strip())
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Recording has no usable audio')
    return value


def silence_cuts(path, length, noise=-42, gap=1.8):
    result = run(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(path), '-af',
                  'silencedetect=noise=%sdB:d=%s' % (noise, gap), '-f', 'null', '-'])
    starts = []
    cuts = [0.0]
    for line in result.stderr.splitlines():
        match = re.search(r'silence_start: ([\d.e+-]+)', line)
        if match:
            starts.append(float(match[1]))
        match = re.search(r'silence_end: ([\d.e+-]+)', line)
        if match and starts:
            start, end = starts.pop(), float(match[1])
            mid = (start + end) / 2
            # Keep the start/end of the recording and do not create tiny tracks.
            if start > 2 and end < length - 2 and mid - cuts[-1] >= 15:
                cuts.append(mid)
    if len(cuts) > 1 and length - cuts[-1] < 15:
        cuts.pop()
    return cuts + [length]


def extract(source, destination, start, end, sample=False):
    if not (math.isfinite(start) and math.isfinite(end) and end > start):
        raise ValueError('Invalid track boundaries')
    command = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
               '-ss', str(start), '-i', str(source), '-t', str(end - start), '-vn']
    if sample:
        command += ['-ar', '44100', '-ac', '1', '-c:a', 'pcm_s16le']
    else:
        command += ['-c:a', 'libmp3lame', '-b:a', '192k']
    run(command + [str(destination)])
