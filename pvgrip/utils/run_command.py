import logging
import subprocess


def run_command(what, cwd, ignore_exitcode = False):
    """Run a system command

    :what: same as subprocess.run argument

    :cwd: run command inside a directory

    :ignore_exitcode: if True: do not interpret non-zero exit code as
    a failed command

    """
    res = subprocess.run(what,
                         cwd = cwd,
                         stderr = subprocess.PIPE,
                         stdout = subprocess.PIPE)

    if ignore_exitcode:
        return

    logging.debug("""
    command = %s
    returns = %d
    stdout  = %s
    stderr  = %s
    """ % (' '.join(what), res.returncode,
           res.stdout.decode(),
           res.stderr.decode()))

    if not res.returncode:
        return

    raise RuntimeError("""
    command has non-zero exit code
    command = %s
    returns = %d
    stdout  = %s
    stderr  = %s
    """ % (' '.join(what), res.returncode,
           res.stdout.decode(),
           res.stderr.decode()))
