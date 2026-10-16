import { Icon } from './icons';

/** Original, code-native artwork: no remote assets or third-party illustration. */
export function CampusScene() {
  return <div className="campus-scene" aria-hidden="true">
    <svg viewBox="0 0 600 280" fill="none">
      <circle cx="452" cy="66" r="33" fill="#F4C78D"/>
      <path d="M66 76h72m-47-9h28M484 115h57" stroke="#B0BEDC" strokeWidth="5" strokeLinecap="round"/>
      <path d="M20 227C122 182 220 248 332 213s165-7 247 19" stroke="#C3D1EA" strokeWidth="2"/>
      <rect x="102" y="119" width="105" height="104" rx="4" fill="#C2CEEA"/>
      <path d="m90 119 64-43 65 43" fill="#7E94C8"/>
      <rect x="392" y="106" width="111" height="117" rx="4" fill="#C2CEEA"/>
      <path d="M385 107h125l-16-18h-92l-17 18Z" fill="#7E94C8"/>
      {[123, 164, 414, 456].map(x => <g key={x}><rect x={x} y="137" width="18" height="21" rx="3" fill="#F8F9FC"/><rect x={x} y="174" width="18" height="21" rx="3" fill="#F8F9FC"/></g>)}
      <path d="M202 215c0-109 195-109 195 0" stroke="#536FB4" strokeWidth="12"/>
      <path d="M225 162v56m37-79v79m38-86v86m37-79v79m37-56v56" stroke="#8BA2D3" strokeWidth="5"/>
      <path d="M185 219h231" stroke="#293F76" strokeWidth="11" strokeLinecap="round"/>
      <path d="M169 240h259m-218 12h82m31 0h62" stroke="#A1B5DE" strokeWidth="3" strokeLinecap="round"/>
      <circle cx="269" cy="188" r="7" fill="#E9AF8E"/><path d="M269 199v17m0-10-10 6m10-6 9 6" stroke="#314778" strokeWidth="6" strokeLinecap="round"/>
      <circle cx="333" cy="187" r="7" fill="#E9AF8E"/><path d="M333 198v18m0-10-9 6m9-6 10 5" stroke="#BC7B54" strokeWidth="6" strokeLinecap="round"/>
      <path d="m62 216 10-38 10 38m449 0 11-47 11 47" stroke="#8DA3CB" strokeWidth="7" strokeLinecap="round"/>
    </svg>
    <span className="scene-note note-knowledge"><Icon name="book" size={18}/>有据可查的校园资料</span>
    <span className="scene-note note-support"><Icon name="users" size={18}/>连接真实的支持</span>
    <span className="scene-caption">A BRIDGE TO YOUR NEXT STEP</span>
  </div>;
}

export function SupportGuide() {
  return <section className="support-guide" aria-label="支持流程">
    <div><span>01</span><Icon name="chat" size={17}/><strong>说说你的问题</strong><p>事务咨询、学习安排或日常感受</p></div>
    <div><span>02</span><Icon name="book" size={17}/><strong>一起找到方向</strong><p>有资料时，展开引用查看原文</p></div>
    <div><span>03</span><Icon name="users" size={17}/><strong>需要时连接人工</strong><p>明确说出你希望获得的帮助</p></div>
  </section>;
}
