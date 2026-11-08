# 电视订阅（SeleneTV）

常规订阅地址：https://raw.githubusercontent.com/Bitithao/tv-subscriptions/main/selene.txt

## 当前维护方式

受限自动维护版本正在进行首轮验收。计划时间为北京时间每天 05:23、11:23、17:23、23:23；GitHub 调度可能延后。不依赖家里的电脑。

- 只检测 allowed-origins.json 中为每个频道选定的服务器。该文件表示访问范围，不是安全认证。
- 抓取 CCSH、vbskycn 清单后，仅接纳同一频道、同一已选服务器上的直接 M3U8 地址。新服务器不会自动访问。
- 仅允许代码中列明的原服务器与 CDN 跳转对应关系，保留媒体签名参数，最多三次跳转；拒绝未核对的跨服务器引用、非媒体入口、HTTPS 降级、HTML/JavaScript 响应、私网地址及停止更新的列表。
- jiduo.me、jdshipin.com 及其子域名继续隔离。普通 CDN 跳转可在单独核查目标后调整规则，不能仅凭可播就放行。
- 每条候选实际读取视频分片，等待直播列表前进后再检查下一片。每频道最多一条主线、一条备用线。
- 翡翠台优先使用已在电视试播并测得 1920×1080、25 fps 的直连候选；清晰度和可用性可能随上游改变。
- 检测失败线路在成功发布时剔除。若整轮异常或翡翠台全部失败，保留上次完整列表并写入失败报告，避免云端网络差异清空电视清单。

公开来源可能失效。HTTP、媒体格式和连续性检查不能保证来源安全，也不能保证云端可播的地址在家庭网络同样可播。

[更新任务](https://github.com/Bitithao/tv-subscriptions/actions/workflows/refresh.yml) · [检测报告](health.json) · [主列表](live.m3u) · [备用列表](backup.m3u)

电视进入「设置 → 立即刷新订阅」可读取配置。安全复核期间固定到旧提交的电视，需要切回上方常规地址才会跟随更新。direct-trial.* 和 hd-trial.* 是临时试播清单，不作为长期订阅使用。

## 点播与隐私

点播来自 hafrey1/LunaTV-config 的 jin18.txt。本仓库不含用户账号、VPN 订阅、节点凭据或观看记录。

## 验证

python -m unittest -v test_refresh.py 运行离线测试。

python update.py 会连接已选服务器并执行完整更新。所有发布通过同一工作流串行执行，失败不提交半套播放列表。
