import { GAME_TEXT_COLORS } from './gameTextColors';

// Native UiView_Plot resources. Source hashes, atlas UVs and dimensions are in
// /game-ui/dialogue/manifest.json; preserve the PNG alpha and aspect ratios.
const root = '/game-ui/dialogue';

export const GAME_DIALOGUE_UI = {
  history: `${root}/SP_PlotSkipBgIcon5.png`,
  manual: `${root}/SP_PlotSkipBgIcon1.png`,
  auto: `${root}/SP_PlotSkipBgIcon3.png`,
  skip: `${root}/SP_PlotSkipBgIcon2.png`,
  hide: `${root}/SP_PlotSkipBgIcon4.png`,
  continue: `${root}/SP_PlotSubtitleNext.png`,
  continueTriangle: `${root}/SP_PlotSubtitleNextB.png`,
  separatorLine: `${root}/SP_ComLineA.png`,
  separatorOrnament: `${root}/SP_ComLineAIcon.png`,
  backdrop: `${root}/SP_PlotBg.png`,
  autoArrow: `${root}/T_PlotAutoArrow.png`,
  autoFrame: `${root}/T_PlotAutoFrame1.png`,
  autoFrameAlternate: `${root}/T_PlotAutoFrame2.png`,
  autoFrameColor1: `${root}/T_PlotAutoFrame_color01.png`,
  autoFrameColor2: `${root}/T_PlotAutoFrame_color02.png`,
  autoFrameMask: `${root}/T_PlotAutoFrame_Mask.png`,
} as const;

export const GAME_DIALOGUE_COLORS = {
  speaker: GAME_TEXT_COLORS.Highlight,
  text: GAME_TEXT_COLORS.WhiteA,
  separator: '#FFFFFF99',
} as const;
