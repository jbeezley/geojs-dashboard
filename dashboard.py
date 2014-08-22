#!/usr/bin/env python

import os
import shutil
import socket
from datetime import datetime
import subprocess as sp

from pymongo import MongoClient


_ctest = '''
set(CTEST_SOURCE_DIRECTORY "{source}")
set(CTEST_BINARY_DIRECTORY "{build}")

include(${{CTEST_SOURCE_DIRECTORY}}/CTestConfig.cmake)
set(CTEST_SITE "{site}")
set(CTEST_BUILD_NAME "{name}")
set(CTEST_CMAKE_GENERATOR "Unix Makefiles")

ctest_start("Experimental")
ctest_configure()
ctest_build()
ctest_test(PARALLEL_LEVEL 1 RETURN_VALUE res)
ctest_coverage()
file(REMOVE ${{CTEST_BINARY_DIRECTORY}}/coverage.xml)
ctest_submit()

if(NOT res EQUAL 0)
  message(FATAL_ERROR "Test failures occurred.")
endif()
'''

_host = socket.gethostname().split('.')[0]


def _communicate(*arg, **kw):
    kw['stderr'] = sp.STDOUT
    kw['stdout'] = sp.PIPE
    kw['shell'] = True
    p = sp.Popen(
        *arg,
        **kw
    )
    out, err = p.communicate()
    return p.returncode, out


def config():
    return {
        'mongo-host': 'lusitania',
        'mongo-port': 27017,
        'mongo-database': 'geojs_dashboard',
        'test-dir': '~/geojs-testing',
        'repo': 'https://github.com/OpenGeoscience/geojs.git'
    }


def run_test(repo, commit, testdir, branch):

    # ======================
    # git clone and checkout
    # ======================
    s, out = _communicate(' '.join([
        'git', 'clone',
        '--recursive',
        repo, testdir
    ]))
    if s != 0:
        return (False, 'clone "%s" failed' % repo, out)

    s, out = _communicate(' '.join([
        'git',
        '-C', testdir,
        'checkout',
        commit
    ]))
    if s != 0:
        return (False, 'checkout "%s" failed' % commit, out)

    s, out = _communicate(' '.join([
        'git',
        '-C', testdir,
        'submodule', 'update'
    ]))
    if s != 0:
        return (False, 'submodule update failed', out)

    # =========
    # configure
    # =========
    builddir = os.path.join(testdir, '_build')
    os.makedirs(builddir)
    s, out = _communicate(
        ' '.join([
            'cmake',
            '-D', 'SELENIUM_TESTS=ON',
            '-D', 'CHROME_TESTS=OFF',
            '-D', 'FIREFOX_TESTS=ON',
            '-D', 'COVERAGE_TESTS=OFF',
            '..'
        ]),
        cwd=builddir
    )
    if s != 0:
        return (False, 'cmake configure failed', out)

    # ==============
    # build and test
    # ==============
    build_script = os.path.join(builddir, 'build.cmake')
    kw = {
        'source': testdir,
        'build': builddir,
        'site': _host,
        'name': branch + '-' + commit[:6]
    }
    open(build_script, 'w').write(
        _ctest.format(**kw)
    )
    s, out = _communicate(
        'ctest -VV -S {}'.format(build_script),
        cwd=builddir
    )
    test_result = s
    test_output = out

    if test_result != 0:
        return (False, 'Test(s) failed', test_output)

    return (True, 'All tests passed!', test_output)


def start_test(item, oldTest=None):
    if oldTest:
        status = {
            'pass': oldTest['status']['pass'],
            'output': oldTest['status']['output'],
            'reason': 'Already tested in branch %s' % oldTest['branch'],
            'skipped': True
        }
    else:
        cfg = config()
        basedir = os.path.expanduser(cfg['test-dir'])
        testdir = os.path.join(basedir, item['commit'])
        shutil.rmtree(testdir, ignore_errors=True)
        try:
            os.makedirs(testdir)
        except OSError:
            pass
        result = run_test(cfg['repo'], item['commit'], testdir, item['branch'])

        status = {
            'pass': result[0],
            'reason': result[1],
            'output': result[2],
            'skipped': False
        }

    return status


def notify(item, status):
    pass


def main():
    cfg = config()
    cl = MongoClient(
        host=cfg['mongo-host'],
        port=cfg['mongo-port'],
    )
    db = cl[cfg['mongo-database']]
    queue = db['queue']
    results = db['results']

    for item in queue.find():
        oldTest = results.find_one({'commit': item['commit']})
        status = start_test(item, oldTest)
        if not oldTest:
            result = dict(item)
            result.pop('_id')
            result['time'] = datetime.now()
            result['status'] = status
            results.insert(result)
        queue.remove(item)
        notify(item, status)


if __name__ == '__main__':
    import sys
    main(*sys.argv[1:])