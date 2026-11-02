import re
import time

from alert_feed import AlertFeed
from discord_threads import clip
from repository_summary import open_items


def summary(github,binding):
    en=binding.config.get('language')=='en'
    sections=[]
    for kind,label,path in [('pr','PR','pulls'),('issue','Issue','issues'),('discussion','Discussion','discussions')]:
        items=open_items(github,binding.repo,kind)
        lines=['**'+label+' · '+str(len(items))+(' open**' if en else '개 열림**')]
        for item in items[:3]:
            title=clip(str(item['title']).replace('\n',' ').replace('@','@\u200b'),100)
            title=re.sub(r'([\\`*_{}\[\]()<>~|])',r'\\\1',title)
            lines.append('• ['+title+'](https://github.com/'+binding.repo+'/'+path+'/'+str(int(item['number']))+')')
        if not items:
            lines.append('None.' if en else '없음')
        if len(items)>3:
            lines.append(('… '+str(len(items)-3)+' more' if en else '… 그 외 '+str(len(items)-3)+'개'))
        sections.append('\n'.join(lines))
    return '\n\n'.join(sections)+('\n\nUse /summary for the full roundup.' if en else '\n\n전체 현황은 /summary로 확인해 주세요.')


class ConnectionWelcome:
    def __init__(self,service,binding,api,queue):
        self.service,self.binding,self.api,self.queue=service,binding,api,queue
        self.next_tick=0

    def phase(self):
        with self.service.registry.db() as db:
            row=db.execute('SELECT phase FROM connection_welcomes WHERE binding=?',(self.binding.id,)).fetchone()
        return row[0] if row else 2

    def pending(self):
        return bool(self.binding.config.get('alert_channel')) and self.phase()<2

    def tick(self,github):
        if time.monotonic()<self.next_tick:
            return
        self.next_tick=time.monotonic()+60
        binding=self.binding
        if not self.pending() or self.service.registry.get(binding.id)!=binding:
            return
        en=binding.config.get('language')=='en'
        feed=AlertFeed(self.api,binding.config,self.queue)
        key='connection:'+binding.config['guild_id']+':'+str(binding.repo_id)
        link='https://github.com/'+binding.repo
        if self.phase()==0:
            feed.send(key,'welcome',
                '🧵 Hi! I’m Threadle. I’ll bring you updates from **'+binding.repo+'** here from now on.' if en else '🧵 안녕하세요! Threadle이에요. 지금부터 **'+binding.repo+'** 저장소의 소식을 전해드릴게요.',
                'We’re connected!' if en else '연결됐어요!',
                'Let’s start with a quick look at your repository.' if en else '먼저 현재 저장소 현황을 간단히 살펴볼까요?',link,0x96B86A)
            with self.service.registry.db() as db:
                db.execute('UPDATE connection_welcomes SET phase=1 WHERE binding=? AND phase=0',(binding.id,))
        description=summary(github,binding)
        if self.service.registry.get(binding.id)!=binding:
            return
        feed.send(key,'welcome-summary',
            '🧵 Here’s where things stand right now.' if en else '🧵 지금 저장소는 이런 상태예요.',
            binding.repo+' · '+('Current roundup' if en else '현재 현황'),clip(description,2900),link,0x0969DA)
        with self.service.registry.db() as db:
            db.execute('UPDATE connection_welcomes SET phase=2 WHERE binding=?',(binding.id,))
