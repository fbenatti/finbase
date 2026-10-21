import {
  ArrowDownward, ArrowUpward, Close, DensityLargeOutlined, DensityMediumOutlined, DensitySmallOutlined,
  FileDownloadOutlined, FilterListOutlined, MoreVertOutlined, SearchOutlined, SwapVertOutlined, ViewColumnOutlined, VisibilityOffOutlined,
} from "@mui/icons-material";

type IconProps = { className?: string };
const Ascending = ({ className }: IconProps) => <ArrowUpward className={className} sx={{ fontSize: 16 }} />;
const Descending = ({ className }: IconProps) => <ArrowDownward className={className} sx={{ fontSize: 16 }} />;
const Unsorted = ({ className }: IconProps) => <SwapVertOutlined className={className} sx={{ fontSize: 16 }} />;
const More = ({ className }: IconProps) => <MoreVertOutlined className={className} sx={{ fontSize: 20 }} />;
const FilterIcon = ({ className }: IconProps) => <FilterListOutlined className={className} sx={{ fontSize: 20 }} />;
const Columns = ({ className }: IconProps) => <ViewColumnOutlined className={className} sx={{ fontSize: 20 }} />;
const Export = ({ className }: IconProps) => <FileDownloadOutlined className={className} sx={{ fontSize: 20 }} />;
const Find = ({ className }: IconProps) => <SearchOutlined className={className} sx={{ fontSize: 20 }} />;
const Clear = ({ className }: IconProps) => <Close className={className} sx={{ fontSize: 20 }} />;
const Hide = ({ className }: IconProps) => <VisibilityOffOutlined className={className} sx={{ fontSize: 20 }} />;

/** Shared by every grid through the application theme, including lazy modules. */
const Compact = ({ className }: IconProps) => <DensitySmallOutlined className={className} sx={{ fontSize: 20 }} />;
const Standard = ({ className }: IconProps) => <DensityMediumOutlined className={className} sx={{ fontSize: 20 }} />;
const Comfortable = ({ className }: IconProps) => <DensityLargeOutlined className={className} sx={{ fontSize: 20 }} />;
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
