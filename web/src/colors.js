// One color per tool number, shared by the plan list and the 3D toolpath.
const TOOL_COLORS = ['#e4572e', '#17bebb', '#f2b705', '#76b041', '#a06cd5', '#2e86ab', '#f18f01', '#c73e1d']

export const toolColor = (number) => TOOL_COLORS[(number ?? 0) % TOOL_COLORS.length]
