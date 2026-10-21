import { SvgIcon, type SvgIconProps } from "@mui/material";
import type { ElementType } from "react";
import {
  Add as CarbonAdd, Cloud as CarbonCloud, DataBase, Download as CarbonDownload,
  Book, Application, Store, Search as CarbonSearch, Renew, Edit as CarbonEdit,
  TrashCan, Time, ConnectionSignal, Filter, OverflowMenuVertical, ChevronDown,
  Play, Upload, Save as CarbonSave, CheckmarkOutline, ArrowsHorizontal, Copy, Maximize, Table, View, StopFilled, Restart, Checkmark, CloseOutline, MisuseOutline, ChevronRight as CarbonChevronRight, Subtract, ThumbsUp, Undo as CarbonUndo, ArrowLeft,
} from "@carbon/react/icons";

// Preserve MUI sizing, colors and focus styling while using one Carbon icon family.
const icon = (Component: ElementType) => function FinbaseIcon(props: SvgIconProps) {
  return <SvgIcon component={Component} inheritViewBox {...props} />;
};
export const Add = icon(CarbonAdd);
export const Cloud = icon(CarbonCloud);
export const Storage = icon(DataBase);
export const Dataset = Storage;
export const Download = icon(CarbonDownload);
export const MenuBook = icon(Book);
export const AutoAwesome = icon(Application);
export const Psychology = AutoAwesome;
export const SmartToy = AutoAwesome;
export const Storefront = icon(Store);
export const Search = icon(CarbonSearch);
export const Refresh = icon(Renew);
export const Edit = icon(CarbonEdit);
export const Delete = icon(TrashCan);
export const History = icon(Time);
export const Cable = icon(ConnectionSignal);
export const FilterAlt = icon(Filter);
export const More = icon(OverflowMenuVertical);
export const ExpandMore = icon(ChevronDown);
export const PlayArrow = icon(Play);
export const CloudUpload = icon(Upload);
export const Save = icon(CarbonSave);
export const Validate = icon(CheckmarkOutline);

export const Close = icon(CloseOutline);
export const CompareArrows = icon(ArrowsHorizontal);
export const ContentCopy = icon(Copy);
export const OpenInFull = icon(Maximize);
export const TableChart = icon(Table);
export const Visibility = icon(View);
export const Stop = icon(StopFilled);
export const Replay = icon(Restart);
export const Check = icon(Checkmark);
export const Block = icon(MisuseOutline);
export const Cancel = icon(CloseOutline);
export const ChevronRight = icon(CarbonChevronRight);
export const Remove = icon(Subtract);
export const ThumbUp = icon(ThumbsUp);
export const DeleteForever = Delete;
export const Undo = icon(CarbonUndo);
export const Schedule = History;
export const ArrowBack = icon(ArrowLeft);
