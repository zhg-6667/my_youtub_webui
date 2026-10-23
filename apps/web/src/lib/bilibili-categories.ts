export type BilibiliCategory = {
  tid: number
  name: string
  group: string
}

export type BilibiliCategoryGroup = {
  group: string
  items: BilibiliCategory[]
}

export const BILIBILI_CATEGORY_GROUPS: BilibiliCategoryGroup[] = [
  {
    group: "游戏",
    items: [
      { tid: 17, name: "单机游戏", group: "游戏" },
      { tid: 171, name: "电子竞技", group: "游戏" },
      { tid: 172, name: "手机游戏", group: "游戏" },
      { tid: 65, name: "网络游戏", group: "游戏" },
      { tid: 173, name: "桌游棋牌", group: "游戏" },
      { tid: 121, name: "GMV", group: "游戏" },
      { tid: 136, name: "音游", group: "游戏" },
    ],
  },
  {
    group: "知识",
    items: [
      { tid: 201, name: "科学科普", group: "知识" },
      { tid: 124, name: "社科·法律·心理", group: "知识" },
      { tid: 228, name: "人文历史", group: "知识" },
      { tid: 207, name: "财经商业", group: "知识" },
      { tid: 208, name: "校园学习", group: "知识" },
      { tid: 209, name: "职业职场", group: "知识" },
      { tid: 229, name: "设计·创意", group: "知识" },
      { tid: 122, name: "野生技能协会", group: "知识" },
    ],
  },
  {
    group: "科技 / 数码",
    items: [
      { tid: 95, name: "数码", group: "科技 / 数码" },
      { tid: 230, name: "软件应用", group: "科技 / 数码" },
      { tid: 231, name: "计算机技术", group: "科技 / 数码" },
      { tid: 232, name: "科工机械", group: "科技 / 数码" },
      { tid: 233, name: "极客 DIY", group: "科技 / 数码" },
    ],
  },
  {
    group: "动画",
    items: [
      { tid: 24, name: "MAD·AMV", group: "动画" },
      { tid: 25, name: "MMD·3D", group: "动画" },
      { tid: 47, name: "同人·手书", group: "动画" },
      { tid: 257, name: "配音", group: "动画" },
      { tid: 210, name: "手办·模玩", group: "动画" },
      { tid: 86, name: "特摄", group: "动画" },
      { tid: 27, name: "动画综合", group: "动画" },
    ],
  },
  {
    group: "音乐 / 舞蹈",
    items: [
      { tid: 28, name: "原创音乐", group: "音乐 / 舞蹈" },
      { tid: 31, name: "翻唱", group: "音乐 / 舞蹈" },
      { tid: 30, name: "VOCALOID·UTAU", group: "音乐 / 舞蹈" },
      { tid: 59, name: "演奏", group: "音乐 / 舞蹈" },
      { tid: 29, name: "音乐现场", group: "音乐 / 舞蹈" },
      { tid: 130, name: "音乐综合", group: "音乐 / 舞蹈" },
      { tid: 20, name: "宅舞", group: "音乐 / 舞蹈" },
      { tid: 198, name: "街舞", group: "音乐 / 舞蹈" },
      { tid: 200, name: "中国舞", group: "音乐 / 舞蹈" },
      { tid: 154, name: "舞蹈综合", group: "音乐 / 舞蹈" },
    ],
  },
  {
    group: "生活 / 美食 / 动物",
    items: [
      { tid: 138, name: "搞笑", group: "生活 / 美食 / 动物" },
      { tid: 21, name: "日常", group: "生活 / 美食 / 动物" },
      { tid: 161, name: "手工", group: "生活 / 美食 / 动物" },
      { tid: 162, name: "绘画", group: "生活 / 美食 / 动物" },
      { tid: 76, name: "美食制作", group: "生活 / 美食 / 动物" },
      { tid: 212, name: "美食侦探", group: "生活 / 美食 / 动物" },
      { tid: 213, name: "美食测评", group: "生活 / 美食 / 动物" },
      { tid: 218, name: "喵星人", group: "生活 / 美食 / 动物" },
      { tid: 219, name: "汪星人", group: "生活 / 美食 / 动物" },
      { tid: 221, name: "野生动物", group: "生活 / 美食 / 动物" },
      { tid: 75, name: "动物综合", group: "生活 / 美食 / 动物" },
    ],
  },
  {
    group: "影视 / 娱乐",
    items: [
      { tid: 182, name: "影视杂谈", group: "影视 / 娱乐" },
      { tid: 183, name: "影视剪辑", group: "影视 / 娱乐" },
      { tid: 85, name: "短片", group: "影视 / 娱乐" },
      { tid: 184, name: "预告·资讯", group: "影视 / 娱乐" },
      { tid: 71, name: "综艺", group: "影视 / 娱乐" },
      { tid: 137, name: "明星综合", group: "影视 / 娱乐" },
      { tid: 241, name: "娱乐杂谈", group: "影视 / 娱乐" },
    ],
  },
  {
    group: "鬼畜",
    items: [
      { tid: 22, name: "鬼畜调教", group: "鬼畜" },
      { tid: 26, name: "音 MAD", group: "鬼畜" },
      { tid: 126, name: "人力 VOCALOID", group: "鬼畜" },
      { tid: 216, name: "鬼畜剧场", group: "鬼畜" },
      { tid: 127, name: "教程演示", group: "鬼畜" },
    ],
  },
]

export const BILIBILI_CATEGORIES = BILIBILI_CATEGORY_GROUPS.flatMap((group) => group.items)
