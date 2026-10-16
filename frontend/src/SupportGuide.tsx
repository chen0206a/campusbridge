import { Icon } from './icons';

export function ServiceDirectory() {
  const entries = [
    { title: '校园资料', description: '预约、借阅、助学与办事说明', icon: 'book' },
    { title: '学生对话', description: '校园咨询、学习安排与多轮记录', icon: 'chat' },
    { title: '人工协助', description: '提交事项，由辅导员认领与处理', icon: 'users' },
  ];
  return <section className="service-directory" aria-label="功能目录">
    <header><span>功能目录</span><span>01 — 03</span></header>
    {entries.map((entry, index) => <div className="directory-row" key={entry.title}>
      <span className="directory-number">0{index + 1}</span><Icon name={entry.icon} size={21}/>
      <div><h2>{entry.title}</h2><p>{entry.description}</p></div>
    </div>)}
    <footer><Icon name="book" size={15}/>资料引用可查看原文和版本</footer>
  </section>;
}

export function SupportGuide() {
  return <section className="support-guide" aria-label="支持流程">
    <div><span>01</span><Icon name="chat" size={17}/><strong>提交问题</strong><p>在输入框描述具体事项</p></div>
    <div><span>02</span><Icon name="book" size={17}/><strong>查看引用</strong><p>有引用时，点击查看原文</p></div>
    <div><span>03</span><Icon name="users" size={17}/><strong>申请人工协助</strong><p>在对话中提出人工帮助需求</p></div>
  </section>;
}
