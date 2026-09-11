import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  acceptContract,
  cancelContract,
  fulfillContract,
  getContract,
  getContracts,
  type ContractFilters,
} from "../api/contracts";

export function useContracts(filters: ContractFilters = {}) {
  return useQuery({ queryKey: ["contracts", filters], queryFn: () => getContracts(filters) });
}

export function useContract(contractId: number | null) {
  return useQuery({
    queryKey: ["contracts", "detail", contractId],
    queryFn: () => getContract(contractId!),
    enabled: contractId != null,
  });
}

function useContractAction(action: (id: number) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (contractId: number) => action(contractId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["contracts"] });
      queryClient.invalidateQueries({ queryKey: ["economy"] });
    },
  });
}

export function useAcceptContract() {
  return useContractAction(acceptContract);
}

export function useFulfillContract() {
  return useContractAction(fulfillContract);
}

export function useCancelContract() {
  return useContractAction(cancelContract);
}
