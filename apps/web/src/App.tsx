import { RouterProvider } from "react-router-dom";
import { router } from "./routes";
import { AuthProvider } from "./features/auth/auth-context";

export default function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}
