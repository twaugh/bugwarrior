# -*- coding: utf-8 -*-
import copy
import unittest

import taskw.task

from bugwarrior.config import BugwarriorConfigParser
from bugwarrior import db

from .base import ConfigTest


class TestMergeLeft(unittest.TestCase):
    def setUp(self):
        self.issue_dict = {'annotations': ['testing']}

    def assertMerged(self, local, remote, **kwargs):
        db.merge_left('annotations', local, remote, **kwargs)
        self.assertEqual(local, remote)

    def test_with_dict(self):
        self.assertMerged({}, self.issue_dict)

    def test_with_taskw(self):
        self.assertMerged(taskw.task.Task({}), self.issue_dict)

    def test_already_in_sync(self):
        self.assertMerged(self.issue_dict, self.issue_dict)

    def test_rough_equality_hamming_false(self):
        """ When hamming=False, rough equivalents are duplicated. """
        remote = {'annotations': ['\n  testing  \n']}

        db.merge_left('annotations', self.issue_dict, remote, hamming=False)
        self.assertEqual(len(self.issue_dict['annotations']), 2)

    def test_rough_equality_hamming_true(self):
        """ When hamming=True, rough equivalents are not duplicated. """
        remote = {'annotations': ['\n  testing  \n']}

        db.merge_left('annotations', self.issue_dict, remote, hamming=True)
        self.assertEqual(len(self.issue_dict['annotations']), 1)

class TestReplaceLeft(unittest.TestCase):
    def setUp(self):
        self.issue_dict = {'tags': ['test', 'test2'] }
        self.remote = { 'tags': ['remote_tag1', 'remote_tag2'] }

    def assertReplaced(self, local, remote, **kwargs):
        db.replace_left('tags', local, remote, **kwargs)
        self.assertEqual(local, remote)

    def test_with_dict(self):
        self.assertReplaced({}, self.issue_dict)

    def test_with_taskw(self):
        self.assertReplaced(taskw.task.Task({}), self.issue_dict)

    def test_already_in_sync(self):
        self.assertReplaced(self.issue_dict, self.issue_dict)

    def test_replace(self):
        self.assertReplaced(self.issue_dict, self.remote)

    def test_replace_with_keeped_item(self):
        """ When keeped_item is set, all item in this list are keeped """
        result = {'tags': ['test', 'remote_tag1', 'remote_tag2'] }
        print(self.issue_dict)
        keeped_items = [ 'test' ]
        db.replace_left('tags', self.issue_dict, self.remote, keeped_items)
        self.assertEqual(self.issue_dict, result)


class TestSynchronize(ConfigTest):

    def test_synchronize(self):

        def remove_non_deterministic_keys(tasks):
            for status in ['pending', 'completed']:
                for task in tasks[status]:
                    del task['modified']
                    del task['entry']
                    del task['uuid']

            return tasks

        def get_tasks(tw):
            return remove_non_deterministic_keys(tw.load_tasks())

        rawconfig = BugwarriorConfigParser()
        rawconfig.add_section('general')
        rawconfig.set('general', 'targets', 'my_service')
        rawconfig.set('general', 'static_fields', 'project, priority')
        rawconfig.add_section('my_service')
        rawconfig.set('my_service', 'service', 'github')

        tw = taskw.TaskWarrior(self.taskrc)
        self.assertEqual(tw.load_tasks(), {'completed': [], 'pending': []})

        issue = {
            'description': 'Blah blah blah. ☃',
            'project': 'sample_project',
            'githubtype': 'issue',
            'githuburl': 'https://example.com',
            'priority': 'M',
        }

        # TEST NEW ISSUE AND EXISTING ISSUE.
        for _ in range(2):
            # Use an issue generator with two copies of the same issue.
            # These should be de-duplicated in db.synchronize before
            # writing out to taskwarrior.
            # https://github.com/ralphbean/bugwarrior/issues/601
            issue_generator = iter((issue, issue,))
            db.synchronize(issue_generator, rawconfig, 'general')

            self.assertEqual(get_tasks(tw), {
                'completed': [],
                'pending': [{
                    u'project': u'sample_project',
                    u'priority': u'M',
                    u'status': u'pending',
                    u'description': u'Blah blah blah. ☃',
                    u'githuburl': u'https://example.com',
                    u'githubtype': u'issue',
                    u'id': 1,
                    u'urgency': 4.9,
                }]})

        # TEST CHANGED ISSUE.
        issue['description'] = 'Yada yada yada.'

        # Change static field
        issue['project'] = 'other_project'

        db.synchronize(iter((issue,)), rawconfig, 'general')

        self.assertEqual(get_tasks(tw), {
            'completed': [],
            'pending': [{
                u'priority': u'M',
                u'project': u'sample_project',
                u'status': u'pending',
                u'description': u'Yada yada yada.',
                u'githuburl': u'https://example.com',
                u'githubtype': u'issue',
                u'id': 1,
                u'urgency': 4.9,
            }]})

        # TEST CLOSED ISSUE.
        db.synchronize(iter(()), rawconfig, 'general')

        completed_tasks = tw.load_tasks()

        tasks = remove_non_deterministic_keys(copy.deepcopy(completed_tasks))
        del tasks['completed'][0]['end']
        self.assertEqual(tasks, {
            'completed': [{
                u'project': u'sample_project',
                u'description': u'Yada yada yada.',
                u'githubtype': u'issue',
                u'githuburl': u'https://example.com',
                u'id': 0,
                u'priority': u'M',
                u'status': u'completed',
                u'urgency': 4.9,
            }],
            'pending': []})

        # TEST REOPENED ISSUE
        db.synchronize(iter((issue,)), rawconfig, 'general')

        tasks = tw.load_tasks()
        self.assertEqual(
            completed_tasks['completed'][0]['uuid'],
            tasks['pending'][0]['uuid'])

        tasks = remove_non_deterministic_keys(tasks)
        self.assertEqual(tasks, {
            'completed': [],
            'pending': [{
                u'priority': u'M',
                u'project': u'sample_project',
                u'status': u'pending',
                u'description': u'Yada yada yada.',
                u'githuburl': u'https://example.com',
                u'githubtype': u'issue',
                u'id': 1,
                u'urgency': 4.9,
            }]})


class TestUDAs(ConfigTest):
    def test_udas(self):
        rawconfig = BugwarriorConfigParser()
        rawconfig.add_section('general')
        rawconfig.set('general', 'targets', 'my_service')
        rawconfig.add_section('my_service')
        rawconfig.set('my_service', 'service', 'github')

        udas = sorted(list(
            db.get_defined_udas_as_strings(rawconfig, 'general')))
        self.assertEqual(udas, [
            u'uda.githubbody.label=Github Body',
            u'uda.githubbody.type=string',
            u'uda.githubclosedon.label=GitHub Closed',
            u'uda.githubclosedon.type=date',
            u'uda.githubcreatedon.label=Github Created',
            u'uda.githubcreatedon.type=date',
            u'uda.githubmilestone.label=Github Milestone',
            u'uda.githubmilestone.type=string',
            u'uda.githubnamespace.label=Github Namespace',
            u'uda.githubnamespace.type=string',
            u'uda.githubnumber.label=Github Issue/PR #',
            u'uda.githubnumber.type=numeric',
            u'uda.githubrepo.label=Github Repo Slug',
            u'uda.githubrepo.type=string',
            u'uda.githubstate.label=GitHub State',
            u'uda.githubstate.type=string',
            u'uda.githubtitle.label=Github Title',
            u'uda.githubtitle.type=string',
            u'uda.githubtype.label=Github Type',
            u'uda.githubtype.type=string',
            u'uda.githubupdatedat.label=Github Updated',
            u'uda.githubupdatedat.type=date',
            u'uda.githuburl.label=Github URL',
            u'uda.githuburl.type=string',
            u'uda.githubuser.label=Github User',
            u'uda.githubuser.type=string',
        ])
