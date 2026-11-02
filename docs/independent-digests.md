# Independent daily and weekly digests

/settings daily_digest:true weekly_digest:true enables both snapshots. Each switch can be disabled independently. digest_hour controls daily time; weekly_digest_hour and digest_weekday control weekly time. Both use utc_offset. These remain snapshots of open PR/Issue state at delivery, not historical activity reports. Both schedules can produce messages on the same day and at the same hour.

Flat Discord options are arranged as repository/language/time zone, reminders/batching, daily switch/time, weekly switch/day/time, Do Not Disturb switch/start/end. Descriptions have group prefixes. Settings replies and the web form use separate sections. Existing option names for times and Do Not Disturb remain unchanged. The old exclusive digest option is no longer advertised; cached legacy invocations are still accepted.

Reading a legacy digest=off/daily/weekly configuration resolves to the same enabled schedules without updating any binding. The weekly hour falls back to the old shared hour. Changes write independent daily_digest/weekly_digest flags. Activation timestamps are tracked separately: changing one schedule does not rearm the other or resend an already delivered period. Existing team_schedule and alert receipt keys remain valid, including restart and lost-response recovery.

New switches do not enable themselves just because a time/day is edited. Existing servers retain their current choices until an administrator explicitly changes them. Web and command settings apply the same policy. Both scheduler activation and rendering use the resolved independent switches, including connections without reminders, batching or Do Not Disturb.
