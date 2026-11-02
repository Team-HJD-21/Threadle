import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from delivery import Queue,CachedThreads
from discord_threads import Topic,make_embed,topic_from,marker
from notification_layout import group_payload,message_pages,hydrate_issue_fields
from repository_summary import all_repository_summaries
from service import Service
from test_service import config
from test_discord_threads import FakeDiscord

class ReadableNotificationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.queue=Queue(self.root/'queue.sqlite3')

    def payload(self,kind='pr',action='opened',number=1):
        obj={'number':number,'title':'Clear title','body':'The original description.','user':{'login':'author'},'created_at':'2026-11-01T01:00:00Z','updated_at':'2026-11-01T01:00:02Z','labels':[],'assignees':[],'milestone':None,'type':None,'issue_fields':[]}
        if kind=='pr':obj.update(head={'ref':'feature/x'},base={'ref':'main'},draft=False,requested_reviewers=[],requested_teams=[])
        if kind=='discussion':obj.update(category={'name':'Q&A'},labels=[{'name':'help'}])
        return {'action':action,{'pr':'pull_request','issue':'issue','discussion':'discussion'}[kind]:obj,'repository':{'id':11,'full_name':'one/repo'},'installation':{'id':101},'sender':{'login':'editor'}}

    def test_three_creation_templates_only_show_supported_fields(self):
        for kind in ['pr','issue','discussion']:
            p=self.payload(kind);obj=p[{'pr':'pull_request','issue':'issue','discussion':'discussion'}[kind]]
            embed=make_embed(topic_from('one/repo',kind,obj),{'pr':'pull_request','issue':'issues','discussion':'discussion'}[kind],p)
            names=[field['name'] for field in embed['fields']]
            self.assertEqual(embed['description'],'The original description.')
            self.assertNotIn('<t:',embed['description'])
            self.assertEqual(embed['timestamp'],'2026-11-01T01:00:00+00:00')
            self.assertEqual(embed['author']['name'],'author')
            if kind=='pr':self.assertEqual(names[:2],['Branches','Details'])
            if kind=='issue':self.assertIn('Issue fields',names)
            if kind=='discussion':
                self.assertIn('Category',embed['fields'][0]['value']);self.assertIn('Labels',names)
                self.assertNotIn('Assignees',names);self.assertNotIn('Milestone',names)

    def test_issue_type_and_custom_fields_keep_values_without_repeating_description(self):
        p=self.payload('issue','typed');p['issue']['type']={'name':'Task'}
        topic=topic_from('one/repo','issue',p['issue'])
        self.assertIn('Task',make_embed(topic,'issues',p)['description'])
        for name,value in [('Priority','High'),('Effort',0)]:
            p.update(action='field_added',field={'name':name},field_value=value)
            result=make_embed(topic,'issues',p)
            self.assertIn(name+': '+str(value),result['description'])
            self.assertNotIn('original description',result['description'])
            self.assertNotIn('새 활동',result['title'])

    def test_real_api_field_shapes_use_option_name_and_zero(self):
        p=self.payload('issue');del p['issue']['issue_fields']
        topic=topic_from('one/repo','issue',p['issue'])
        api=Mock();api.pages.return_value=[{'issue_field_name':'Priority','value':1,'single_select_option':{'name':'High'}},{'issue_field_name':'Effort','value':0}]
        hydrated=hydrate_issue_fields(topic,p,api)
        result=make_embed(hydrated,'issues',p)
        fields=next(f['value'] for f in result['fields'] if f['name']=='Issue fields')
        self.assertIn('Priority: High',fields);self.assertIn('Effort: 0',fields)

    def test_short_window_durable_group_prioritizes_description_and_folds_creation_options(self):
        assigned=self.payload(action='assigned');assigned['assignee']={'login':'dev'};assigned['pull_request']['assignees']=[{'login':'dev'}]
        opened=self.payload()
        labeled=self.payload(action='labeled');labeled['label']={'name':'bug'};labeled['pull_request'].update(assignees=[{'login':'dev'}],labels=[{'name':'bug'}])
        with patch('delivery.time.time',return_value=100):self.queue.put('a','pull_request',assigned)
        with patch('delivery.time.time',return_value=102):self.queue.put('b','pull_request',opened)
        with patch('delivery.time.time',return_value=103):self.queue.put('c','pull_request',labeled)
        with patch('delivery.time.time',return_value=107):self.assertIsNone(self.queue.group(self.queue.next(),8))
        with patch('delivery.time.time',return_value=109):job,ids=self.queue.group(self.queue.next(),8)
        payload=json.loads(job[2]);topic=topic_from('one/repo','pr',payload['pull_request'])
        result=make_embed(topic,job[1],payload)
        self.assertEqual(len(result['_embeds']),1)
        self.assertEqual(result['_embeds'][0]['description'],'The original description.')
        self.assertIn('bug',str(result['_embeds'][0]['fields']))
        restarted=Queue(self.queue.path)
        with patch('delivery.time.time',return_value=110):replay,again=restarted.group(restarted.next(),8)
        self.assertEqual(replay,job);self.assertEqual(again,ids)
        restarted.finish_group(ids)
        self.assertIsNone(restarted.next())
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs WHERE payload IS NOT NULL').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM delivery_groups WHERE payload IS NOT NULL').fetchone()[0],0)

    def test_late_arrival_cannot_change_frozen_retry_group_and_other_topics_stay_separate(self):
        with patch('delivery.time.time',return_value=100):self.queue.put('one','issues',self.payload('issue'))
        with patch('delivery.time.time',return_value=109):first,ids=self.queue.group(self.queue.next(),8)
        with patch('delivery.time.time',return_value=110):
            self.queue.put('late','issues',self.payload('issue','labeled'))
            self.queue.put('other','issues',self.payload('issue',number=2))
            replay,again=self.queue.group(self.queue.next(),8)
        self.assertEqual(first,replay);self.assertEqual(ids,['one'])
        self.queue.finish_group(ids)
        with patch('delivery.time.time',return_value=120):next_job,ids=self.queue.group(self.queue.next(),8)
        self.assertEqual(ids,['late'])

    def test_one_group_sends_one_thread_message_and_one_alert_even_after_retry(self):
        service=Service(self.root,{'DISCORD_BOT_TOKEN':'test'})
        identity=service.registry.add('team',11,'one/repo',101,'app',config(1000))
        binding=service.registry.get(identity)
        q=service.queue(binding)
        with patch('delivery.time.time',return_value=100):
            q.put('opened','pull_request',self.payload())
            value=self.payload(action='labeled');value['label']={'name':'bug'};q.put('label','pull_request',value)
        threads,feed,github=Mock(),Mock(),Mock()
        threads.deliver.return_value='thread'
        github.call.return_value={'id':11,'full_name':'one/repo'}
        service.github=lambda _:github
        with patch('delivery.time.time',return_value=109):self.assertTrue(service.process(binding,threads,feed,window_seconds=8))
        self.assertEqual(threads.deliver.call_count,1);self.assertEqual(feed.topic.call_count,1)
        self.assertIsNone(q.next())

    def test_long_group_pages_respect_discord_limits_and_receipt_recovery(self):
        entries=[]
        for n in range(12):
            p=self.payload(action='created');p['comment']={'body':str(n)+' text '+('x'*2000)}
            entries.append(('issue_comment',p))
        p=group_payload(entries);topic=topic_from('one/repo','pr',p['pull_request'])
        embed=make_embed(topic,'threadle_bundle',p)
        pages=message_pages(embed)
        self.assertGreater(len(pages),1)
        for page in pages:
            self.assertLessEqual(len(page),10)
            self.assertLess(sum(len(e.get('description',''))+len(e.get('title',''))+sum(len(f['name'])+len(f['value']) for f in e.get('fields',[])) for e in page),5800)
        api=FakeDiscord();threads=CachedThreads(api,'g',{'pr':'c'},self.queue);threads.bot={'id':'bot'}
        threads.deliver(topic,embed,'group-key',lambda:False)
        count=sum(len(rows) for rows in api.posts.values())
        threads.deliver(topic,embed,'group-key',lambda:False)
        self.assertEqual(sum(len(rows) for rows in api.posts.values()),count)
        last=list(api.posts.values())[0][-1]
        self.assertTrue(marker(last)[-1].startswith('github-event:'))

    def test_summary_all_pr_titles_and_oldest_ten_other_items_across_repositories(self):
        bindings=[type('B',(),{'repo':repo,'config':{'language':'en'}})() for repo in ['one/repo','two/repo']]
        service=Mock()
        def items(api,repo,kind):
            return [{'number':n,'title':kind+' title '+str(n),'createdAt':f'2026-01-{n:02}'} for n in range(1,26)]
        with patch('repository_summary.open_items',side_effect=items):pages=all_repository_summaries(service,bindings)
        result='\\n'.join(pages)
        for repo in ['one/repo','two/repo']:
            self.assertIn('https://github.com/'+repo+'/pull/25',result)
            self.assertIn('https://github.com/'+repo+'/issues/10',result)
            self.assertNotIn('https://github.com/'+repo+'/issues/11',result)
            self.assertNotIn('https://github.com/'+repo+'/discussions/11',result)
        self.assertIn('15 remaining issues',result)
        self.assertGreater(len(pages),2)
        self.assertTrue(all(len(page.encode('utf-16-le'))//2<=1850 for page in pages))

    def test_github_html_and_markdown_gifs_become_images_with_fallback_links(self):
        from notification_media import media_embeds
        url='https://github.com/user-attachments/assets/abc-123'
        result=media_embeds({'description':'Review\n<img width="480" alt="demo" src="'+url+'" />\n![Next](https://example.com/demo.gif)','url':'https://github.com/one/repo/pull/1'})
        self.assertEqual(result[0]['image']['url'],url)
        self.assertEqual(result[1]['image']['url'],'https://example.com/demo.gif')
        self.assertNotIn('<img',result[0]['description'])
        self.assertIn(url,result[0]['description'])
        self.assertEqual(media_embeds({'description':'<img src="file:///private">'})[0].get('image'),None)

    def test_summary_command_sends_all_pages_as_private_cards_and_skips_acknowledged_pages(self):
        from test_command_jobs import CommandTests
        fixture=CommandTests();fixture.setUp()
        try:
            fixture.jobs.execute=Mock(return_value=['**one/repo**\nPR first','**one/repo · PR**\nPR last'])
            fixture.jobs.enqueue(fixture.payload('summary'))
            fixture.jobs.process()
            messages=[call for call in fixture.api.call.call_args_list if call.args[0] in ('PATCH','POST') and '/webhooks/' in call.args[1]]
            self.assertEqual(len(messages),2)
            self.assertIn('embeds',messages[0].args[2])
            self.assertEqual(messages[1].args[2]['flags'],64)
            self.assertIn('PR last',messages[1].args[2]['embeds'][0]['description'])
            fixture.jobs.process()
            after=[call for call in fixture.api.call.call_args_list if call.args[0] in ('PATCH','POST') and '/webhooks/' in call.args[1]]
            self.assertEqual(len(after),2)
        finally:fixture.tearDown()

    def test_global_summary_command_accepts_discord_omitted_empty_options(self):
        from command_sync import matches
        self.assertTrue(matches({'name':'summary','options':[]},{'name':'summary'}))
        self.assertFalse(matches({'name':'summary','options':[]},{'name':'summary','options':[{'name':'type'}]}))

    def test_description_and_many_fields_split_within_utf16_limits(self):
        p=self.payload('issue');p['issue']['body']='🧵'*1600
        p['issue']['issue_fields']={('Field '+str(n)):('X'*200) for n in range(70)}
        topic=topic_from('one/repo','issue',p['issue'])
        pages=message_pages(make_embed(topic,'issues',p))
        self.assertGreater(len(pages),1)
        flattened=[e for page in pages for e in page]
        self.assertIn('Field 69',str(flattened))
        for page in pages:
            units=lambda text:len(text.encode('utf-16-le'))//2
            size=sum(units(e.get('title',''))+units(e.get('description',''))+sum(units(f['name'])+units(f['value']) for f in e.get('fields',[])) for e in page)
            self.assertLess(size,5800)
            self.assertTrue(all(len(e.get('fields',[]))<=25 for e in page))

    def test_issue_lifecycle_bundle_headings_show_last_state(self):
        from notification_layout import bundle_embed,group_payload
        for language,labels in [('en',{'closed':'Closed','deleted':'Deleted','reopened':'Reopened'}),('ko',{'closed':'Closed','deleted':'Deleted','reopened':'Reopened'})]:
            for action,label in labels.items():
                payload=self.payload('issue',action)
                topic=topic_from('one/repo','issue',payload['issue'])
                result=bundle_embed(topic,group_payload([('issues',payload)]),language)
                self.assertIn('ISSUE #'+str(topic.number)+' · '+label,result['_embeds'][0]['title'])
        events=[('issues',self.payload('issue',action)) for action in ('closed','reopened')]
        payload=group_payload(events)
        result=bundle_embed(topic_from('one/repo','issue',events[-1][1]['issue']),payload,'en')
        self.assertIn('Reopened',result['_embeds'][0]['title'])
        self.assertNotIn('Closed',result['_embeds'][0]['title'])

