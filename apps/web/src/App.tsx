import { RouterProvider } from "react-router-dom";
import { router } from "./routes";
import { AuthProvider } from "./features/auth/auth-context";
import { CompanyProvider } from "./features/company/company-context";

export default function App() {
  return (
    <AuthProvider>
      <CompanyProvider>
        <RouterProvider router={router} />
      </CompanyProvider>
    </AuthProvider>
  );
}
