import { Box, Typography } from "@mui/material";
import { InboxOutlined as InboxIcon } from "@mui/icons-material";
import { dataDisplayText } from "../utils/dataPresentation";

interface EmptyStateProps {
  message: string;
  icon?: React.ReactNode;
}

export default function EmptyState({ message, icon }: EmptyStateProps) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        py: 8,
        color: "text.secondary",
      }}
    >
      {icon || <InboxIcon sx={{ fontSize: 64, mb: 2, opacity: 0.4 }} />}
      <Typography variant="body1">{dataDisplayText(message)}</Typography>
    </Box>
  );
}
