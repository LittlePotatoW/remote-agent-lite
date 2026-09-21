/**
 * 侧栏滑动的手势判定。
 *
 * 抽成纯函数是为了能单独验证门限：这里只做数学，不碰 DOM。
 */

/** 手指移动超过这个距离才算手势。 */
export const SWIPE_DEAD_ZONE = 6;
/** |dx| 至少是 |dy| 的这个倍数才判为横向。 */
export const SWIPE_AXIS_RATIO = 0.8;
/** 从屏幕左右边缘起手时放宽方向判定。 */
export const SWIPE_EDGE_RATIO = 0.45;
export const SWIPE_EDGE_ZONE = 32;
/** 拖动超过面板宽度的 30% 打开。 */
export const SWIPE_OPEN_RATIO = 0.3;
/** 已打开时反向拖掉 35% 关闭。 */
export const SWIPE_CLOSE_RATIO = 0.35;
/** 快速轻扫：位移够且速度够，忽略百分比阈值。 */
export const SWIPE_FLICK_DISTANCE = 32;
export const SWIPE_FLICK_VELOCITY = 0.45;

export type SwipeAxis = 'pending' | 'horizontal' | 'vertical';

export function swipeAxis(dx: number, dy: number, startEdge: boolean): SwipeAxis {
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);
  if (absX < SWIPE_DEAD_ZONE && absY < SWIPE_DEAD_ZONE) return 'pending';
  const ratio = startEdge ? SWIPE_EDGE_RATIO : SWIPE_AXIS_RATIO;
  return absX < absY * ratio ? 'vertical' : 'horizontal';
}

export function swipeStartedAtEdge(x: number, viewportWidth: number): boolean {
  return x <= SWIPE_EDGE_ZONE || x >= viewportWidth - SWIPE_EDGE_ZONE;
}

export function isFlick(dx: number, velocity: number): boolean {
  return Math.abs(dx) >= SWIPE_FLICK_DISTANCE && Math.abs(velocity) >= SWIPE_FLICK_VELOCITY;
}

export interface SwipeRelease {
  /** 松开时面板已经拉出的宽度。 */
  pull: number;
  /** 手势开始时面板已经打开的宽度（0 = 原本是关的）。 */
  base: number;
  /** 当前展开的面板，没有则为 null。 */
  panel: 'tree' | 'files' | null;
  /** 手势结束时的总横向位移，向右为正。 */
  lastDx: number;
  /** 最近一段的位移速度，px/ms。 */
  recentVelocity: number;
  width: number;
}

/** 松手后是否保持展开。 */
export function swipeKeepsOpen(release: SwipeRelease): boolean {
  const { pull, base, panel, lastDx, recentVelocity, width } = release;
  const opening = !base || (panel === 'tree' ? lastDx > 0 : lastDx < 0);
  if (isFlick(lastDx, recentVelocity)) return opening;
  if (opening) return pull >= width * SWIPE_OPEN_RATIO;
  return pull > width * (1 - SWIPE_CLOSE_RATIO);
}
