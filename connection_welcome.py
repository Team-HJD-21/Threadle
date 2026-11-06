import re
import time

from alert_feed import AlertFeed
from discord_threads import clip
from repository_summary import open_items


def guide_text(english):
    if english:
        return (
            '**One item, one thread**\n'
            'Open a PR, issue or discussion on GitHub. Threadle creates its Discord thread and keeps comments, reviews and current status together. '
            'The starter card and thread title update as the work progresses.\n\n'
            '**Connect your account**\n'
            'Run `/account connect` and follow the private link. Each member connects their own GitHub account and chooses mentions and comment forwarding. '
            'Linking alone does not enable forwarding. `/account status` checks your link; `/account disconnect` removes it in all servers.\n\n'
            '**Reply from Discord**\n'
            'With forwarding enabled, write in a Threadle PR, issue or discussion thread to post as yourself on GitHub. '
            'Reply to a code/file review card to join that exact review conversation. After the card reflects your reply, Threadle removes the input if it has Manage Messages permission. '
            'A ✅ means the GitHub post succeeded. Deleting your forwarded Discord input also requests deletion of its GitHub comment.\n\n'
            '**Settings and summaries**\n'
            'Use `/settings` to adjust notifications, `/settings` without options to view current values and `/settings-history` to see recent changes. '
            'Run commands in the repository’s category, or explicitly select the repository. `/summary` shows its current roundup. '
            'Daily/weekly digests, PR reminders and Do Not Disturb are optional. CI/CD failures and merge conflict warnings remain immediate.\n\n'
            '**Channels and access**\n'
            'Channel names can be changed later. GitHub content, including private text and images, is visible to anyone who can view the linked Discord channels. '
            'Check category/channel permissions before inviting more members. This guide is posted once per server.\n\n'
            '[Questions or feedback? Join Threadle Support](https://discord.gg/6faZbNBdv)')
    return (
        '**하나의 항목, 하나의 스레드**\n'
        'GitHub에서 PR·Issue·Discussion을 만들면 Discord 스레드가 생성돼요. 댓글과 리뷰는 같은 스레드에 모이고, '
        '진행 상태는 시작 카드와 스레드 제목에 반영돼요.\n\n'
        '**내 계정 연결하기**\n'
        '`/account connect`의 개인 링크를 열어 각자 GitHub 계정을 연결해 주세요. 자동 멘션과 댓글 전달을 선택할 수 있어요. '
        '계정 연결만으로 댓글 전달이 켜지지는 않아요. `/account status`로 확인하고, `/account disconnect`로 모든 서버에서 개인 연결을 해제할 수 있어요.\n\n'
        '**Discord에서 답장하기**\n'
        '댓글 전달을 켜면 Threadle의 PR·Issue·Discussion 스레드에 쓴 메시지가 내 GitHub 계정의 댓글로 올라가요. '
        '코드·파일 리뷰 카드에 답장하면 해당 리뷰 대화에 등록돼요. 카드에 답글이 반영된 뒤 입력 메시지를 정리하려면 봇에 메시지 관리 권한이 필요해요. '
        '✅는 GitHub 등록 성공 표시예요. 전달된 Discord 원본을 직접 삭제하면 GitHub 댓글 삭제도 요청돼요.\n\n'
        '**알림 설정과 현황 확인**\n'
        '`/settings`로 알림을 설정하고, 옵션 없는 `/settings`로 현재 값, `/settings-history`로 최근 변경을 확인해요. '
        '해당 저장소 카테고리 안에서 실행하거나 저장소를 직접 지정해 주세요. `/summary`는 현재 현황을 보여줘요. '
        '일일·주간 요약, PR 재알림, 방해금지 시간은 선택해서 사용할 수 있어요. CI/CD 실패와 Merge conflict 경고는 즉시 전달돼요.\n\n'
        '**채널과 공개 범위**\n'
        '채널 이름은 나중에 원하는 이름으로 바꿔도 돼요. 비공개 저장소의 본문·이미지도 연결된 Discord 채널을 볼 수 있는 구성원에게 공개돼요. '
        '카테고리·채널 권한을 확인해 주세요. 이 안내는 서버당 한 번만 보내요.\n\n'
        '[질문·피드백은 Threadle Support에서](https://discord.gg/6faZbNBdv)')


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
        return self.guide_pending() or bool(self.binding.config.get('alert_channel')) and self.phase()<2

    def guide_pending(self):
        with self.service.registry.db() as db:
            return db.execute('SELECT channel FROM guild_guides WHERE binding=? AND sent=0',(self.binding.id,)).fetchone()

    def send_guide(self):
        row = self.guide_pending()
        if not row:
            return
        from discord_threads import ApiError
        binding = self.binding
        config = dict(binding.config, alert_channel=row[0])
        feed = AlertFeed(self.api,config,self.queue)
        en = config.get('language') == 'en'
        try:
            feed.check()
            feed.send('guild-guide:'+config['guild_id'],'getting-started-v1','',
                      'Welcome to Threadle' if en else 'Threadle 사용 안내',guide_text(en),
                      'https://threadle.teamhjd.com',0x96B86A,silent=True)
        except ApiError as error:
            if error.status != 404:
                raise
            # Respect an administrator deleting the optional guide channel.
        with self.service.registry.db() as db:
            db.execute('UPDATE guild_guides SET sent=1 WHERE binding=?',(binding.id,))

    def tick(self,github):
        if time.monotonic()<self.next_tick:
            return
        self.next_tick=time.monotonic()+60
        binding=self.binding
        if not self.pending() or self.service.registry.get(binding.id)!=binding:
            return
        en=binding.config.get('language')=='en'
        self.send_guide()
        if not binding.config.get('alert_channel') or self.phase()>=2:
            return
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
