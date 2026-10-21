import {
  ArrowDown, ArrowUp, ArrowsVertical, Close, Column, Download,
  Filter, OverflowMenuVertical, Search, ViewOff, RowCollapse, Row, RowExpand,
} from "@carbon/react/icons";

type IconProps = { className?: string };
const Ascending = ({ className }: IconProps) => <ArrowUp size={16} className={className} />;
const Descending = ({ className }: IconProps) => <ArrowDown size={16} className={className} />;
const Unsorted = ({ className }: IconProps) => <ArrowsVertical size={16} className={className} />;
const More = ({ className }: IconProps) => <OverflowMenuVertical size={20} className={className} />;
const FilterIcon = ({ className }: IconProps) => <Filter size={20} className={className} />;
const Columns = ({ className }: IconProps) => <Column size={20} className={className} />;
const Export = ({ className }: IconProps) => <Download size={20} className={className} />;
const Find = ({ className }: IconProps) => <Search size={20} className={className} />;
const Clear = ({ className }: IconProps) => <Close size={20} className={className} />;
const Hide = ({ className }: IconProps) => <ViewOff size={20} className={className} />;

/** Shared by every grid through the application theme, including lazy modules. */
const Compact = ({ className }: IconProps) => <RowCollapse size={20} className={className} />;
const Standard = ({ className }: IconProps) => <Row size={20} className={className} />;
const Comfortable = ({ className }: IconProps) => <RowExpand size={20} className={className} />;
export const dataGridIcons = {
  densityCompactIcon: Compact, densityStandardIcon: Standard, densityComfortableIcon: Comfortable,
  columnSortedAscendingIcon: Ascending, columnSortedDescendingIcon: Descending,
  columnUnsortedIcon: Unsorted, columnMenuIcon: More, moreActionsIcon: More,
  columnFilteredIcon: FilterIcon, openFilterButtonIcon: FilterIcon,
  columnSelectorIcon: Columns, exportIcon: Export,
  quickFilterIcon: Find, quickFilterClearIcon: Clear,
  columnMenuSortAscendingIcon: Ascending, columnMenuSortDescendingIcon: Descending,
  columnMenuFilterIcon: FilterIcon, columnMenuManageColumnsIcon: Columns,
  columnMenuHideIcon: Hide, columnMenuClearIcon: Clear,
};
